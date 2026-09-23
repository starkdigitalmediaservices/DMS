"use client";
import React, { useState } from "react";
import Link from "next/link";
import {
  Plus,
  Home,
  ChevronRight,
  HardDrive,
  Users,
  Clock,
  Star,
  Trash2,
  FolderPlus,
  Upload,
  Sparkles,
  Server,
  AlertTriangle,
  ShieldCheck,
} from "lucide-react";
import type { DriveStats, FolderTreeNode, DocumentListItem } from "@/types";
import { FolderTreeSidebar } from "./FolderTreeSidebar";
import { useI18n } from "@/lib/i18n";

interface DriveSidebarProps {
  currentView: "home" | "my-drive" | "recent" | "starred" | "trash" | "shared" | "chat" | "needs-review";
  onSelectView: (view: "home" | "my-drive" | "recent" | "starred" | "trash" | "shared" | "chat" | "needs-review") => void;
  onOpenNewFolderModal: () => void;
  onTriggerFileUpload: () => void;
  onOpenConnectorModal: () => void;
  stats: DriveStats | null;
  folderTree?: FolderTreeNode[];
  activeFolderId?: string | null;
  onSelectFolder?: (folderId: string) => void;
  onSelectDoc?: (doc: DocumentListItem) => void;
  onPreviewDoc?: (doc: DocumentListItem) => void;
}

export function DriveSidebar({
  currentView,
  onSelectView,
  onOpenNewFolderModal,
  onTriggerFileUpload,
  onOpenConnectorModal,
  stats,
  folderTree = [],
  activeFolderId = null,
  onSelectFolder,
  onSelectDoc,
  onPreviewDoc,
}: DriveSidebarProps) {
  const { t } = useI18n();
  const [showNewMenu, setShowNewMenu] = useState(false);
  const [expandDriveTree, setExpandDriveTree] = useState(true);

  const formatSize = (bytes: number) => {
    if (!bytes) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  };

  const usedBytes = stats?.total_bytes ?? stats?.total_size_bytes ?? 0;

  return (
    <aside className="w-60 flex-shrink-0 flex flex-col justify-between py-2 pr-2 select-none bg-gdriveBg overflow-y-auto">
      <div>
        {/* "+ New" Action Button */}
        <div className="relative mb-4 px-3">
          <button
            onClick={() => setShowNewMenu(!showNewMenu)}
            className="flex items-center gap-3 px-4 py-3 bg-white hover:bg-[#f1f3f4] text-[#1f1f1f] rounded-2xl shadow-md border border-[#c4c7c5] hover:shadow-lg transition-all duration-200 group"
          >
            <Plus className="w-6 h-6 text-[#1f1f1f] stroke-[2.5]" />
            <span className="font-semibold text-sm pr-2">{t("drive.nav.new", "New")}</span>
          </button>

          {showNewMenu && (
            <>
              <div role="presentation" className="fixed inset-0 z-40" onClick={() => setShowNewMenu(false)} />
              <div className="absolute left-3 top-14 z-50 w-56 bg-white rounded-2xl shadow-xl border border-[#e1e3e1] p-2 animate-fadeIn text-sm text-[#1f1f1f]">

                <button
                  onClick={() => {
                    setShowNewMenu(false);
                    onOpenNewFolderModal();
                  }}
                  className="flex items-center gap-3 w-full px-3 py-2.5 rounded-xl hover:bg-[#f0f4f9] font-medium text-left"
                >
                  <FolderPlus className="w-4 h-4 text-[#0d2e5c]" />
                  <span>{t("drive.nav.new_folder", "New folder")}</span>
                </button>

                <div className="h-px bg-[#e1e3e1] my-1" />

                <button
                  onClick={() => {
                    setShowNewMenu(false);
                    onTriggerFileUpload();
                  }}
                  className="flex items-center gap-3 w-full px-3 py-2.5 rounded-xl hover:bg-[#f0f4f9] font-medium text-left"
                >
                  <Upload className="w-4 h-4 text-[#00639b]" />
                  <span>{t("drive.nav.file_upload", "File upload")}</span>
                </button>

                <div className="h-px bg-[#e1e3e1] my-1" />

                <button
                  onClick={() => {
                    setShowNewMenu(false);
                    onOpenConnectorModal();
                  }}
                  className="flex items-center gap-3 w-full px-3 py-2.5 rounded-xl hover:bg-[#f0f4f9] font-medium text-left"
                >
                  <Server className="w-4 h-4 text-[#34a853]" />
                  <span>{t("drive.nav.connect_device", "Connect a device")}</span>
                </button>
              </div>
            </>
          )}
        </div>

        {/* Navigation Section Items */}
        <nav className="space-y-0.5">
          {/* Home */}
          <button
            onClick={() => onSelectView("home")}
            className={`flex items-center gap-4 w-full px-4 py-2 rounded-r-full text-sm font-medium transition-all ${currentView === "home"
              ? "bg-[#c2e7ff] text-[#001d35] font-bold"
              : "text-[#444746] hover:bg-[#edf2fc] hover:text-[#1f1f1f]"
              }`}
          >
            <Home className="w-4 h-4" />
            <span>{t("drive.nav.home", "Home")}</span>
          </button>

          {/* AI Chat */}
          <button
            onClick={() => onSelectView("chat")}
            className={`flex items-center gap-4 w-full px-4 py-2 rounded-r-full text-sm font-medium transition-all ${currentView === "chat"
              ? "bg-[#c2e7ff] text-[#001d35] font-bold"
              : "text-[#444746] hover:bg-[#edf2fc] hover:text-[#1f1f1f]"
              }`}
          >
            <Sparkles className="w-4 h-4 text-[#0d2e5c]" />
            <span>{t("drive.nav.chat", "AI Chat")}</span>
          </button>

          {/* My Drive Node */}
          <div>
            <div
              className={`flex items-center justify-between w-full pr-4 py-2 rounded-r-full text-sm font-medium transition-all ${currentView === "my-drive" && !activeFolderId
                ? "bg-[#c2e7ff] text-[#001d35] font-bold"
                : "text-[#444746] hover:bg-[#edf2fc] hover:text-[#1f1f1f]"
                }`}
            >
              <button
                type="button"
                onClick={() => onSelectView("my-drive")}
                className="flex items-center gap-4 flex-1 pl-4 py-0 text-left cursor-pointer"
              >
                <HardDrive className="w-4 h-4" />
                <span>{t("drive.nav.my_drive", "My Drive")}</span>
              </button>
              {folderTree.length > 0 && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setExpandDriveTree(!expandDriveTree);
                  }}
                  className="p-1 rounded-full hover:bg-black/10 transition-transform"
                  aria-label={expandDriveTree ? "Collapse My Drive folder tree" : "Expand My Drive folder tree"}
                  aria-expanded={expandDriveTree}
                >
                  <ChevronRight
                    className={`w-3.5 h-3.5 transition-transform ${expandDriveTree ? "rotate-90" : ""}`}
                  />
                </button>
              )}
            </div>

            {/* Subfolder Tree */}
            {expandDriveTree && folderTree.length > 0 && onSelectFolder && (
              <FolderTreeSidebar
                tree={folderTree}
                activeFolderId={activeFolderId}
                onSelectFolder={onSelectFolder}
                onSelectDoc={onSelectDoc}
                onPreviewDoc={onPreviewDoc}
              />
            )}
          </div>

          <div className="h-px bg-[#e1e3e1] my-2 mx-4" />

          {/* Starred */}
          <button
            onClick={() => onSelectView("starred")}
            className={`flex items-center gap-4 w-full px-4 py-2 rounded-r-full text-sm font-medium transition-all ${currentView === "starred"
              ? "bg-[#c2e7ff] text-[#001d35] font-bold"
              : "text-[#444746] hover:bg-[#edf2fc] hover:text-[#1f1f1f]"
              }`}
          >
            <Star className="w-4 h-4" />
            <span>{t("drive.nav.starred", "Starred")}</span>
          </button>

          {/* Needs Review */}
          <button
            onClick={() => onSelectView("needs-review")}
            className={`flex items-center gap-4 w-full px-4 py-2 rounded-r-full text-sm font-medium transition-all ${currentView === "needs-review"
              ? "bg-[#c2e7ff] text-[#001d35] font-bold"
              : "text-[#444746] hover:bg-[#edf2fc] hover:text-[#1f1f1f]"
              }`}
          >
            <AlertTriangle className="w-4 h-4 text-amber-500" />
            <span>Needs Review</span>
          </button>

          {/* Workbench — real gap found live 2026-09-09: this page
              (app/workbench/page.tsx) was fully built, including the
              Join Mismatches tab, but was never linked from anywhere in
              the nav, so it was unreachable from the UI despite the
              underlying data (the fact-level adjudication queue) being
              real and live. A separate route, not an internal drive
              view, so it navigates via Link rather than onSelectView. */}
          <Link
            href="/workbench"
            className="flex items-center gap-4 w-full px-4 py-2 rounded-r-full text-sm font-medium transition-all text-[#444746] hover:bg-[#edf2fc] hover:text-[#1f1f1f]"
          >
            <ShieldCheck className="w-4 h-4" />
            <span>{t("drive.nav.workbench", "Workbench")}</span>
          </Link>

          {/* Bin */}
          <button
            onClick={() => onSelectView("trash")}
            className={`flex items-center gap-4 w-full px-4 py-2 rounded-r-full text-sm font-medium transition-all ${currentView === "trash"
              ? "bg-[#c2e7ff] text-[#001d35] font-bold"
              : "text-[#444746] hover:bg-[#edf2fc] hover:text-[#1f1f1f]"
              }`}
          >
            <Trash2 className="w-4 h-4" />
            <span>{t("drive.nav.trash", "Bin")}</span>
          </button>
        </nav>
      </div>

      {/* Bottom Storage Meter */}
      <div className="px-4 py-3 border-t border-[#e1e3e1]/60">
        <div className="text-xs font-semibold text-[#1f1f1f] mb-1">{t("drive.storage.title", "Storage")}</div>
        <div className="text-xs text-[#444746]">
          {formatSize(usedBytes)} {t("drive.storage.used", "used")}
        </div>
      </div>
    </aside>
  );
}
