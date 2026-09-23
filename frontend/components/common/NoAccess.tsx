"use client";
import React from "react";
import { ShieldAlert } from "lucide-react";

interface NoAccessProps {
  title?: string;
  message?: string;
  children?: React.ReactNode;
  compact?: boolean;
}

/** Shared "you don't have access" state — used both for role gates
 *  (a page/action the current role can't use) and for 403/404 responses
 *  on folders/documents outside the user's department scope. */
export function NoAccess({
  title = "You don't have access",
  message = "Your role doesn't include access to this. Ask an IT admin if you think you should.",
  children,
  compact = false,
}: NoAccessProps) {
  return (
    <div
      role="status"
      className={`flex flex-col items-center justify-center text-center gap-2 rounded-2xl border border-amber-200 bg-amber-50 text-amber-800 ${
        compact ? "p-4" : "p-10"
      }`}
    >
      <ShieldAlert className={compact ? "w-6 h-6" : "w-10 h-10"} aria-hidden="true" />
      <p className="font-semibold text-sm">{title}</p>
      <p className="text-xs max-w-md">{message}</p>
      {children}
    </div>
  );
}
