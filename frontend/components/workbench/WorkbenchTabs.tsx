"use client";
import Link from "next/link";
import { ListChecks, Files } from "lucide-react";

/** Workbench's two ways of working: triage values across documents (Queue),
 *  or check one document end to end against its scan (Documents). */
export default function WorkbenchTabs({ active }: { active: "queue" | "documents" }) {
  const tabs = [
    { key: "queue", href: "/workbench", label: "Queue", icon: ListChecks },
    { key: "documents", href: "/workbench?tab=documents", label: "Documents", icon: Files },
  ] as const;
  return (
    <nav aria-label="Workbench views" className="flex gap-1 rounded-full bg-[#f0f4f9] p-1">
      {tabs.map(({ key, href, label, icon: Icon }) => (
        <Link
          key={key}
          href={href}
          aria-current={active === key ? "page" : undefined}
          className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold transition-colors ${
            active === key ? "bg-[#0d2e5c] text-white" : "text-[#444746] hover:bg-white"
          }`}
        >
          <Icon className="w-3.5 h-3.5" aria-hidden="true" />
          {label}
        </Link>
      ))}
    </nav>
  );
}
