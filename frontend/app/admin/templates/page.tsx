"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  LayoutTemplate,
  Plus,
  Pencil,
  Trash2,
  X,
  Loader2,
  AlertCircle,
  ShieldAlert,
} from "lucide-react";
import { api } from "@/lib/api";
import { useRole } from "@/lib/permissions";
import { Button } from "@/components/ui/Button";
import type { TemplateResponse, TemplateFieldDef, TemplateCreatePayload } from "@/types";

const FIELD_TYPES = ["string", "number", "blob"];
const FIELD_ROLES = [
  { value: "", label: "— none —" },
  { value: "serial", label: "serial (row number)" },
  { value: "continuation_text", label: "continuation_text" },
  { value: "chain_anchor", label: "chain_anchor" },
];
const LAYOUTS = ["single_page", "spread"];

const emptyField = (): TemplateFieldDef => ({ name: "", type: "string", required: false, role: "" });

interface FormState {
  form_type: string;
  era_label: string;
  layout: string;
  fields: TemplateFieldDef[];
}

const emptyForm = (): FormState => ({
  form_type: "",
  era_label: "",
  layout: "single_page",
  fields: [emptyField()],
});

export default function TemplatesAdminPage() {
  const [templates, setTemplates] = useState<TemplateResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm());
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  // useRole() reads the cached profile after mount (not during render —
  // that caused a real hydration mismatch here, React #418/#423).
  const { can: roleCan, ready: roleReady } = useRole();
  const isAdmin = roleCan("templates.manage");

  const fetchTemplates = async () => {
    setLoading(true);
    setError("");
    try {
      const data = await api.templates.list();
      setTemplates(data);
    } catch (err: any) {
      setError(err.message || "Failed to load templates");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTemplates();
  }, []);

  const openCreate = () => {
    setEditingId(null);
    setForm(emptyForm());
    setFormError("");
    setShowForm(true);
  };

  const openEdit = (t: TemplateResponse) => {
    setEditingId(t.id);
    setForm({
      form_type: t.form_type,
      era_label: t.era_label,
      layout: t.layout,
      fields: t.field_schema.length ? t.field_schema.map((f) => ({ ...f, role: f.role || "" })) : [emptyField()],
    });
    setFormError("");
    setShowForm(true);
  };

  const closeForm = () => {
    setShowForm(false);
    setEditingId(null);
    setFormError("");
  };

  const updateField = (idx: number, patch: Partial<TemplateFieldDef>) => {
    setForm((f) => ({
      ...f,
      fields: f.fields.map((field, i) => (i === idx ? { ...field, ...patch } : field)),
    }));
  };

  const addField = () => setForm((f) => ({ ...f, fields: [...f.fields, emptyField()] }));
  const removeField = (idx: number) =>
    setForm((f) => ({ ...f, fields: f.fields.filter((_, i) => i !== idx) }));

  const handleSave = async () => {
    setFormError("");
    if (!form.form_type.trim() || !form.era_label.trim()) {
      setFormError("Form type and era label are both required.");
      return;
    }
    const cleanFields = form.fields
      .map((f) => ({ ...f, name: f.name.trim() }))
      .filter((f) => f.name);
    if (cleanFields.length === 0) {
      setFormError("At least one field with a name is required.");
      return;
    }
    const payload: TemplateCreatePayload = {
      form_type: form.form_type.trim(),
      era_label: form.era_label.trim(),
      layout: form.layout,
      field_schema: cleanFields.map((f) => ({
        name: f.name,
        type: f.type || "string",
        required: !!f.required,
        role: f.role || null,
      })),
    };

    setSaving(true);
    try {
      if (editingId) {
        await api.templates.update(editingId, payload);
      } else {
        await api.templates.create(payload);
      }
      closeForm();
      await fetchTemplates();
    } catch (err: any) {
      setFormError(err.message || "Failed to save template");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (t: TemplateResponse) => {
    if (!confirm(`Delete template "${t.form_type} | ${t.era_label}"? This cannot be undone.`)) return;
    try {
      await api.templates.delete(t.id);
      await fetchTemplates();
    } catch (err: any) {
      alert(err.message || "Failed to delete template");
    }
  };

  return (
    <div className="h-screen overflow-y-auto bg-[#f8f9fa] text-[#1f1f1f]">
      <header className="h-16 px-6 flex items-center justify-between border-b border-[#e1e3e1]/60 bg-white/80 backdrop-blur-md sticky top-0 z-20">
        <div className="flex items-center gap-4">
          <Link
            href="/admin"
            className="flex items-center gap-2 text-sm text-[#444746] hover:text-[#1f1f1f] transition-colors px-3 py-1.5 rounded-lg hover:bg-[#f0f4f9]"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Back to Admin</span>
          </Link>
          <div className="h-5 w-px bg-[#e1e3e1]" />
          <h1 className="text-lg font-bold text-[#1f1f1f] flex items-center gap-2">
            <LayoutTemplate className="w-5 h-5 text-[#0d2e5c]" />
            Form Templates
          </h1>
        </div>

        {isAdmin && (
          <Button variant="primary" size="sm" onClick={openCreate}>
            <Plus className="w-4 h-4 mr-2" />
            New Template
          </Button>
        )}
      </header>

      <main className="max-w-[1100px] mx-auto p-6 md:p-8 space-y-6">
        <p className="text-sm text-[#444746]">
          A template registers a form type so a scanned/handwritten document can be classified and its
          fields (including multi-page tables) extracted automatically. Without a matching template, an
          uploaded document stays <span className="font-mono text-xs">unclassified</span> and no
          structured extraction or table stitching runs on it.
        </p>

        {roleReady && !isAdmin && (
          <div className="glass rounded-xl p-4 flex items-center gap-3 text-amber-700 bg-amber-50 border border-amber-200">
            <ShieldAlert className="w-5 h-5 shrink-0" />
            <p className="text-sm">
              You can view registered templates, but creating, editing, or deleting them requires the IT Admin role.
            </p>
          </div>
        )}

        {loading ? (
          <div className="flex flex-col items-center justify-center py-20 gap-3">
            <Loader2 className="w-8 h-8 text-[#0d2e5c] animate-spin" />
            <p className="text-sm text-[#444746]">Loading templates...</p>
          </div>
        ) : error ? (
          <div className="glass rounded-xl p-6 bg-red-50 border border-red-200 text-red-700 flex items-center gap-3">
            <AlertCircle className="w-5 h-5 shrink-0" />
            <div>
              <p className="font-semibold">Error loading templates</p>
              <p className="text-sm opacity-90">{error}</p>
            </div>
            <Button size="sm" variant="secondary" onClick={fetchTemplates} className="ml-auto">
              Retry
            </Button>
          </div>
        ) : templates.length === 0 ? (
          <div className="glass rounded-xl p-10 text-center space-y-2">
            <LayoutTemplate className="w-8 h-8 text-[#444746] mx-auto" />
            <p className="text-sm text-[#444746]">No templates registered yet.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {templates.map((t) => (
              <div key={t.id} className="glass rounded-xl p-5 space-y-3">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="font-semibold text-[#1f1f1f]">{t.form_type}</p>
                    <p className="text-xs text-[#444746]">
                      {t.era_label} · layout: {t.layout} · {t.field_schema.length} field
                      {t.field_schema.length === 1 ? "" : "s"}
                    </p>
                  </div>
                  {isAdmin && (
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        onClick={() => openEdit(t)}
                        className="p-2 rounded-lg text-[#444746] hover:text-[#0d2e5c] hover:bg-[#f0f4f9] transition-colors"
                        title="Edit"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => handleDelete(t)}
                        className="p-2 rounded-lg text-[#444746] hover:text-red-600 hover:bg-red-50 transition-colors"
                        title="Delete"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  )}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {t.field_schema.map((f) => (
                    <span
                      key={f.name}
                      className="px-2 py-0.5 rounded text-[11px] font-mono bg-[#edf2fc] text-[#0d2e5c] border border-[#0d2e5c]/10"
                      title={f.role ? `role: ${f.role}` : undefined}
                    >
                      {f.name}
                      {f.required ? "*" : ""}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      {showForm && (
        <div className="fixed inset-0 bg-black/40 z-30 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-2xl w-full max-h-[85vh] overflow-y-auto shadow-xl">
            <div className="flex items-center justify-between px-6 py-4 border-b border-[#e1e3e1] sticky top-0 bg-white">
              <h2 className="font-bold text-[#1f1f1f]">
                {editingId ? "Edit Template" : "New Template"}
              </h2>
              <button onClick={closeForm} className="p-1.5 rounded-lg hover:bg-[#f0f4f9] text-[#444746]">
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-6 space-y-4">
              {formError && (
                <div className="rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2">
                  {formError}
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor="tpl-form-type" className="text-xs font-semibold text-[#444746]">
                    Form type
                  </label>
                  <input
                    id="tpl-form-type"
                    className="mt-1 w-full rounded-lg border border-[#e1e3e1] px-3 py-2 text-sm"
                    placeholder="e.g. Waqf Institution Registration File"
                    value={form.form_type}
                    onChange={(e) => setForm((f) => ({ ...f, form_type: e.target.value }))}
                  />
                </div>
                <div>
                  <label htmlFor="tpl-era-label" className="text-xs font-semibold text-[#444746]">
                    Era label
                  </label>
                  <input
                    id="tpl-era-label"
                    className="mt-1 w-full rounded-lg border border-[#e1e3e1] px-3 py-2 text-sm"
                    placeholder="e.g. BPT Act 1950 / Waqf Act 1995"
                    value={form.era_label}
                    onChange={(e) => setForm((f) => ({ ...f, era_label: e.target.value }))}
                  />
                </div>
              </div>

              <div>
                <label htmlFor="tpl-layout" className="text-xs font-semibold text-[#444746]">
                  Layout
                </label>
                <select
                  id="tpl-layout"
                  className="mt-1 w-full rounded-lg border border-[#e1e3e1] px-3 py-2 text-sm"
                  value={form.layout}
                  onChange={(e) => setForm((f) => ({ ...f, layout: e.target.value }))}
                >
                  {LAYOUTS.map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                </select>
                <p className="text-[11px] text-[#444746] mt-1">
                  &quot;spread&quot; is for a register whose entries run across two facing pages; almost
                  every document should use &quot;single_page&quot;.
                </p>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold text-[#444746]">
                    Fields (columns this template reads from each row/page)
                  </p>
                  <button
                    onClick={addField}
                    className="text-xs font-semibold text-[#0d2e5c] hover:underline flex items-center gap-1"
                  >
                    <Plus className="w-3.5 h-3.5" /> Add field
                  </button>
                </div>

                <div className="space-y-2">
                  {form.fields.map((field, idx) => (
                    <div key={idx} className="flex items-center gap-2 bg-[#f8f9fa] rounded-lg p-2">
                      <input
                        className="flex-1 rounded-md border border-[#e1e3e1] px-2 py-1.5 text-sm"
                        placeholder="field_name"
                        value={field.name}
                        onChange={(e) => updateField(idx, { name: e.target.value })}
                      />
                      <select
                        className="rounded-md border border-[#e1e3e1] px-2 py-1.5 text-sm"
                        value={field.type}
                        onChange={(e) => updateField(idx, { type: e.target.value })}
                      >
                        {FIELD_TYPES.map((ty) => (
                          <option key={ty} value={ty}>
                            {ty}
                          </option>
                        ))}
                      </select>
                      <select
                        className="rounded-md border border-[#e1e3e1] px-2 py-1.5 text-sm"
                        value={field.role || ""}
                        onChange={(e) => updateField(idx, { role: e.target.value })}
                      >
                        {FIELD_ROLES.map((r) => (
                          <option key={r.value} value={r.value}>
                            {r.label}
                          </option>
                        ))}
                      </select>
                      <label className="flex items-center gap-1 text-xs text-[#444746] shrink-0">
                        <input
                          type="checkbox"
                          checked={!!field.required}
                          onChange={(e) => updateField(idx, { required: e.target.checked })}
                        />
                        required
                      </label>
                      <button
                        onClick={() => removeField(idx)}
                        disabled={form.fields.length === 1}
                        className="p-1.5 rounded-md text-[#444746] hover:text-red-600 hover:bg-red-50 disabled:opacity-30 disabled:hover:bg-transparent"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-[#e1e3e1] sticky bottom-0 bg-white">
              <Button variant="secondary" size="sm" onClick={closeForm}>
                Cancel
              </Button>
              <Button variant="primary" size="sm" onClick={handleSave} loading={saving}>
                {editingId ? "Save changes" : "Create template"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
