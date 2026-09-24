// Readable names for template field names (wakf_name -> "Waqf name").
// Templates only store machine names today (field_schema has no label), so
// reviewers were shown raw database identifiers. A few register-specific
// words get a fixed rendering; everything else is simply spaced out and
// capitalised, so a new template's fields still read sensibly.
const WORDS: Record<string, string> = {
  sr: "serial",
  no: "no.",
  wakf: "waqf",
  dob: "date of birth",
  dt: "date",
  qty: "quantity",
  mutawalli: "mutawalli (manager)",
};

export function humanFieldName(name: string): string {
  // Only machine identifiers are rewritten; a real column heading read off
  // the scan (e.g. Marathi "पृष्ठे") is shown exactly as read.
  if (!name || !/^[a-z0-9_]+$/.test(name)) return name;
  const words = name
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => {
      const col = /^col(\d+)$/i.exec(w);
      if (col) return `(column ${col[1]})`;
      return WORDS[w.toLowerCase()] ?? w.toLowerCase();
    });
  const text = words.join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export type ConfidenceLevel = "low" | "medium" | "high" | "unknown";

/** "How sure the computer is", in words a first-time reviewer understands. */
export function confidenceInfo(confidence: number | null | undefined): {
  level: ConfidenceLevel; pct: number | null; className: string;
} {
  if (confidence === null || confidence === undefined) {
    return { level: "unknown", pct: null, className: "text-[#444746] bg-[#f0f4f9] border-[#e1e3e1]" };
  }
  const pct = Math.round(confidence * 100);
  if (confidence >= 0.8) return { level: "high", pct, className: "text-emerald-800 bg-emerald-50 border-emerald-200" };
  if (confidence >= 0.5) return { level: "medium", pct, className: "text-amber-800 bg-amber-50 border-amber-200" };
  return { level: "low", pct, className: "text-red-700 bg-red-50 border-red-200" };
}
