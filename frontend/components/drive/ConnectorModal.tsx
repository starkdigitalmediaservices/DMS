"use client";
import React, { useState, useEffect } from "react";
import { X, Copy, Check, Server, Loader2 } from "lucide-react";
import { api } from "@/lib/api";

interface ConnectorModalProps {
  isOpen: boolean;
  onClose: () => void;
}

interface ConnectorInfo {
  sftp: {
    enabled: boolean;
    host: string;
    port: number;
    username: string;
    password: string;
    remote_dir: string;
    note: string;
  };
  email: {
    enabled: boolean;
    address: string;
    smtp_host: string;
    smtp_port: number;
    note: string;
  };
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div>
      <label className="block text-xs font-semibold text-textMuted mb-1">{label}</label>
      <div className="flex items-center gap-2">
        <div className="flex-1 px-3 py-2 bg-surface rounded-lg border border-borderDark text-textMain text-sm font-mono truncate">
          {value}
        </div>
        <button
          onClick={handleCopy}
          className="p-2 rounded-lg border border-borderDark hover:bg-surface text-textMuted hover:text-textMain transition-colors"
          title="Copy"
        >
          {copied ? <Check className="w-4 h-4 text-green-500" /> : <Copy className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
}

export function ConnectorModal({ isOpen, onClose }: ConnectorModalProps) {
  const [info, setInfo] = useState<ConnectorInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"sftp" | "email">("sftp");

  useEffect(() => {
    if (!isOpen) return;
    setLoading(true);
    setError(null);
    api.connectors
      .getInfo()
      .then((data) => setInfo(data))
      .catch(() => setError("Could not load connector details. Please try again."))
      .finally(() => setLoading(false));
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-fadeIn">
      <div className="w-full max-w-lg glass rounded-2xl border border-borderDark p-6 shadow-2xl space-y-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Server className="w-5 h-5 text-primary" />
            <h3 className="text-lg font-bold text-textMain">Connect another device</h3>
          </div>
          <button onClick={onClose} className="p-1 text-textMuted hover:text-textMain">
            <X className="w-5 h-5" />
          </button>
        </div>

        {loading && (
          <div className="flex items-center justify-center py-10 text-textMuted">
            <Loader2 className="w-5 h-5 animate-spin mr-2" /> Loading connection details...
          </div>
        )}

        {error && <div className="text-sm text-red-500 bg-red-500/10 px-4 py-3 rounded-lg">{error}</div>}

        {info && !loading && (
          <>
            <div className="flex gap-1 p-1 bg-surface rounded-xl border border-borderDark w-fit">
              <button
                onClick={() => setActiveTab("sftp")}
                className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  activeTab === "sftp" ? "bg-primary text-white" : "text-textMuted hover:text-textMain"
                }`}
              >
                Folder / SFTP
              </button>
              <button
                onClick={() => setActiveTab("email")}
                className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  activeTab === "email" ? "bg-primary text-white" : "text-textMuted hover:text-textMain"
                }`}
              >
                Email
              </button>
            </div>

            {activeTab === "sftp" && (
              <>
                <p className="text-sm text-textMuted">
                  Give these details to any other computer on your network. Files dropped
                  there will automatically appear in your Drive — no login needed on that device.
                </p>

                <div className="space-y-3">
                  <CopyField label="Host / Address" value={info.sftp.host} />
                  <div className="grid grid-cols-2 gap-3">
                    <CopyField label="Port" value={String(info.sftp.port)} />
                    <CopyField label="Folder" value={info.sftp.remote_dir} />
                  </div>
                  <CopyField label="Username" value={info.sftp.username} />
                  <CopyField label="Password" value={info.sftp.password} />
                </div>

                <div className="text-xs text-textMuted bg-surface rounded-lg px-4 py-3 border border-borderDark">
                  {info.sftp.note}
                </div>

                <div className="text-xs text-textMuted">
                  On the other device, use any SFTP client (FileZilla, WinSCP, or your
                  file manager&apos;s &quot;Connect to Server&quot;) with these details, protocol{" "}
                  <span className="font-semibold text-textMain">SFTP</span>.
                </div>
              </>
            )}

            {activeTab === "email" && (
              <>
                <p className="text-sm text-textMuted">
                  Email a file to this address and it appears in your Drive automatically —
                  no login needed by whoever sends it.
                </p>

                <div className="space-y-3">
                  <CopyField label="Send To" value={info.email.address} />
                </div>

                <div className="text-xs text-textMuted bg-surface rounded-lg px-4 py-3 border border-borderDark">
                  {info.email.note}
                </div>
              </>
            )}

          </>
        )}
      </div>
    </div>
  );
}
