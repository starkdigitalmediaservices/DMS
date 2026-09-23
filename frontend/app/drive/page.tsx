"use client";
import React, { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { DriveTopHeader } from "@/components/drive/DriveTopHeader";
import { DriveSidebar } from "@/components/drive/DriveSidebar";
import { DriveBreadcrumbs } from "@/components/drive/DriveBreadcrumbs";
import { SuggestedFoldersSection } from "@/components/drive/SuggestedFoldersSection";
import { SuggestedFilesTable } from "@/components/drive/SuggestedFilesTable";
import { RightDock } from "@/components/drive/RightDock";
import { DriveDetailPanel } from "@/components/drive/DriveDetailPanel";
import { DocumentPreviewModal } from "@/components/drive/DocumentPreviewModal";
import { NewFolderModal, RenameModal, MoveModal } from "@/components/drive/Modals";
import { ConnectorModal } from "@/components/drive/ConnectorModal";
import { UploadWidget, UploadItem } from "@/components/drive/UploadWidget";
import { AISummary } from "@/components/search/AISummary";
import { ResultCard } from "@/components/search/ResultCard";
import { PersistentChatPanel } from "@/components/chat/PersistentChatPanel";
import { RightSideChatDrawer } from "@/components/chat/RightSideChatDrawer";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import OfflineBanner from "@/components/OfflineBanner";
import OnlineWarningModal from "@/components/OnlineWarningModal";
import { useOnlineStatus } from "@/hooks/useOnlineStatus";
import { offlineStore } from "@/lib/offlineStore";

import { isAuthenticated } from "@/lib/auth";
import { api, isAccessError, ApiError } from "@/lib/api";
import { useRole } from "@/lib/permissions";
import { NoAccess } from "@/components/common/NoAccess";
import { onKeyActivate } from "@/lib/a11y";
import { useDebouncedActivation } from "@/lib/useDebouncedActivation";
import type { Folder, FolderTreeNode, DocumentListItem, DriveStats, SearchResponse, SearchResult } from "@/types";
import { Info, FolderSearch, Eye, Trash2, RotateCcw, Sparkles, FolderPlus, Upload, FolderUp, UploadCloud, Clock, CheckSquare, X, Star, FolderInput, Download, Edit2 } from "lucide-react";


const isUUID = (str: string | null | undefined): boolean => {
  if (!str) return false;
  return /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(str);
};

const getDaysRemainingInBin = (trashedAtStr?: string | null): { text: string; daysLeft: number } => {
  if (!trashedAtStr) return { text: "30 days left", daysLeft: 30 };
  const trashedDate = new Date(trashedAtStr).getTime();
  if (isNaN(trashedDate)) return { text: "30 days left", daysLeft: 30 };
  const now = new Date().getTime();
  const diffDays = Math.floor((now - trashedDate) / (1000 * 60 * 60 * 24));
  const daysLeft = Math.max(0, 30 - diffDays);

  if (daysLeft === 0) return { text: "Deletes today", daysLeft: 0 };
  if (daysLeft === 1) return { text: "1 day left", daysLeft: 1 };
  return { text: `${daysLeft} days left`, daysLeft };
};

export default function DrivePage() {
  const router = useRouter();
  const { isOnline } = useOnlineStatus();
  // Permanent delete (DELETE /documents|folders/{id}) is limited to
  // records_officer / department_head / it_admin server-side.
  const { can: roleCan } = useRole();
  const canDeletePermanent = roleCan("content.deletePermanent");
  // Set when the current folder/view came back 403/404 — i.e. it's outside
  // the user's department grants. Rendered as a "no access" state instead
  // of silently falling back to offline/cached data.
  const [accessDenied, setAccessDenied] = useState(false);
  const [showAIWarningModal, setShowAIWarningModal] = useState(false);
  const [aiWarningFeature, setAiWarningFeature] = useState("AI Assistant");

  // Navigation View & Hierarchy State
  const [currentView, setCurrentView] = useState<"home" | "my-drive" | "recent" | "starred" | "trash" | "shared" | "chat" | "needs-review">("home");
  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);
  const [currentFolder, setCurrentFolder] = useState<Folder | null>(null);
  const [folderPath, setFolderPath] = useState<Folder[]>([]);
  const [folderTree, setFolderTree] = useState<FolderTreeNode[]>([]);

  // Data State
  const [folders, setFolders] = useState<Folder[]>([]);
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [selectedFolder, setSelectedFolder] = useState<Folder | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<DocumentListItem | null>(null);

  // Real bug found live 2026-09-10 (QA report): the detail side panel
  // kept showing a renamed file's OLD name until closed and reopened.
  // selectedDoc/selectedFolder hold their own independent object
  // reference, captured at selection time -- loadContents() refreshes
  // the documents/folders LISTS after a rename (or move, star, etc.) but
  // never touched that separately-held reference, so the panel kept
  // rendering stale data even though the underlying list was correct.
  // Re-deriving the selection from the fresh list by id, whenever either
  // list changes, fixes this for every mutation that goes through
  // loadContents() -- not just rename -- instead of patching each
  // individual handler (rename, move, star, ...) to remember to do this.
  useEffect(() => {
    if (selectedDoc) {
      const fresh = documents.find((d) => d.id === selectedDoc.id);
      if (fresh && fresh !== selectedDoc) setSelectedDoc(fresh);
    }
  }, [documents, selectedDoc]);
  useEffect(() => {
    if (selectedFolder) {
      const fresh = folders.find((f) => f.id === selectedFolder.id);
      if (fresh && fresh !== selectedFolder) setSelectedFolder(fresh);
    }
  }, [folders, selectedFolder]);

  // Multi-Selection State
  const [selectedFolderIds, setSelectedFolderIds] = useState<Set<string>>(new Set());
  const [selectedDocIds, setSelectedDocIds] = useState<Set<string>>(new Set());
  const { handleClick: handleTrashRowClick, handleDoubleClick: handleTrashRowDoubleClick } = useDebouncedActivation();

  // Right-Click Item Context Menu State
  const [itemContextMenu, setItemContextMenu] = useState<{
    isOpen: boolean;
    x: number;
    y: number;
    type: "folder" | "doc";
    item: Folder | DocumentListItem;
  } | null>(null);

  const handleItemContextMenu = (
    e: React.MouseEvent,
    type: "folder" | "doc",
    item: Folder | DocumentListItem
  ) => {
    e.preventDefault();
    e.stopPropagation();

    // If item is ALREADY part of multi-selection, keep current selection!
    const isAlreadySelected =
      type === "folder" ? selectedFolderIds.has(item.id) : selectedDocIds.has(item.id);

    if (!isAlreadySelected) {
      if (type === "folder") {
        handleSelectFolder(item as Folder, false);
      } else {
        handleSelectDoc(item as DocumentListItem, false);
      }
    }

    setItemContextMenu({
      isOpen: true,
      x: Math.min(e.clientX, window.innerWidth - 240),
      y: Math.min(e.clientY, window.innerHeight - 300),
      type,
      item,
    });
  };

  // Document Previewer Modal State
  const [previewDoc, setPreviewDoc] = useState<DocumentListItem | null>(null);

  // UI State
  const [showDetailPanel, setShowDetailPanel] = useState(false);
  const [showRightChatDrawer, setShowRightChatDrawer] = useState(false);
  const [loading, setLoading] = useState(false);
  const [driveStats, setDriveStats] = useState<DriveStats | null>(null);

  // Search State
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResponse, setSearchResponse] = useState<SearchResponse | null>(null);
  const [searching, setSearching] = useState(false);

  // Search Testing Settings (persisted locally — reranker choice + AI summary toggle)
  const [rerankProvider, setRerankProvider] = useState<"bgem3" | "cohere">("cohere");
  const [generateSummary, setGenerateSummary] = useState(true);

  useEffect(() => {
    const savedProvider = typeof window !== "undefined" ? localStorage.getItem("dms_rerank_provider") : null;
    const savedSummary = typeof window !== "undefined" ? localStorage.getItem("dms_generate_summary") : null;
    if (savedProvider === "bgem3" || savedProvider === "cohere") {
      setRerankProvider(savedProvider);
    } else {
      setRerankProvider("cohere");
    }
    if (savedSummary !== null) setGenerateSummary(savedSummary !== "false");
  }, []);

  const handleSetRerankProvider = (v: "bgem3" | "cohere") => {
    setRerankProvider(v);
    if (typeof window !== "undefined") localStorage.setItem("dms_rerank_provider", v);
  };

  const handleSetGenerateSummary = (v: boolean) => {
    setGenerateSummary(v);
    if (typeof window !== "undefined") localStorage.setItem("dms_generate_summary", String(v));
  };

  // Modals State
  const [isNewFolderOpen, setIsNewFolderOpen] = useState(false);
  const [isConnectorModalOpen, setIsConnectorModalOpen] = useState(false);
  const [itemToRename, setItemToRename] = useState<{ type: "folder" | "doc"; item: Folder | DocumentListItem } | null>(null);
  const [itemToMove, setItemToMove] = useState<{ type: "folder" | "doc"; item: Folder | DocumentListItem } | null>(null);

  // Custom Modal Dialog State
  const [confirmModalConfig, setConfirmModalConfig] = useState<{
    isOpen: boolean;
    title?: string;
    message: string;
    type?: "danger" | "warning" | "info" | "success";
    confirmText?: string;
    showCancel?: boolean;
    onConfirm: () => void;
  }>({
    isOpen: false,
    message: "",
    onConfirm: () => {},
  });


  // Uploads & Drag-and-Drop State
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);

  // Context Menu State
  const [contextMenu, setContextMenu] = useState<{ visible: boolean; x: number; y: number }>({
    visible: false,
    x: 0,
    y: 0,
  });

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    if (currentView === "trash" || currentView === "chat" || currentView === "starred" || currentView === "recent" || currentView === "shared") {
      setContextMenu({ visible: false, x: 0, y: 0 });
      return;
    }
    setContextMenu({
      visible: true,
      x: e.clientX,
      y: e.clientY,
    });
  };

  // Drag & Drop Handlers
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (currentView === "trash" || currentView === "chat") return;
    if (e.dataTransfer.types && Array.from(e.dataTransfer.types).includes("Files")) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setIsDragging(false);
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    if (currentView === "trash" || currentView === "chat") return;

    const items = e.dataTransfer.items;
    const entries = items ? Array.from(items).map((item) => item.webkitGetAsEntry?.()).filter(Boolean) : [];
    const hasDirectory = entries.some((entry: any) => entry.isDirectory);

    if (hasDirectory) {
      const nested = await Promise.all(entries.map((entry: any) => readEntryContents(entry, "")));
      await processFolderUpload(nested.flat());
      return;
    }

    const droppedFiles = Array.from(e.dataTransfer.files || []);
    if (droppedFiles.length > 0) {
      processFilesForUpload(droppedFiles);
    }
  };



  // Auth Protection
  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/login");
    }
  }, [router]);

  // Fetch Folder Tree
  const loadFolderTree = async () => {
    try {
      const tree = await api.folders.getTree();
      setFolderTree(tree);
    } catch (err) {
      console.warn("Folder tree load notice:", err);
    }
  };

  // Load Folder Hierarchy Path
  const updateBreadcrumbPath = async (folderId: string | null) => {
    if (!folderId || !isUUID(folderId)) {
      setFolderPath([]);
      setCurrentFolder(null);
      return;
    }

    try {
      const curr = await api.folders.get(folderId);
      setCurrentFolder(curr);

      const path: Folder[] = [];
      let pId = curr.parent_id;
      while (pId && isUUID(pId)) {
        try {
          const parent = await api.folders.get(pId);
          path.unshift(parent);
          pId = parent.parent_id;
        } catch {
          break;
        }
      }
      setFolderPath(path);
    } catch (err) {
      // Out-of-department folders come back 404 here, while their child
      // listings come back 200 [] — so this is where "no access" shows up.
      if (isAccessError(err)) setAccessDenied(true);
      console.warn("Could not resolve breadcrumb path:", err);
    }
  };

  // Fetch Contents Safely
  const loadContents = async () => {
    setLoading(true);
    setAccessDenied(false);
    try {
      api.documents.getStats().then((s) => setDriveStats(s)).catch(() => {});
      loadFolderTree();

      const validParentId = isUUID(currentFolderId) ? currentFolderId : null;
      updateBreadcrumbPath(validParentId);

      if (currentView === "starred") {
        const [fList, dList] = await Promise.all([
          api.folders.list({ is_starred: true, is_trashed: false }),
          api.documents.list({ is_starred: true, is_trashed: false }),
        ]);
        setFolders(fList);
        setDocuments(dList);
      } else if (currentView === "trash") {
        const [fList, dList] = await Promise.all([
          api.folders.list({ is_trashed: true }),
          api.documents.list({ is_trashed: true }),
        ]);
        setFolders(fList);
        setDocuments(dList);
      } else if (currentView === "needs-review") {
        const dList = await api.documents.list({ include_all: true, is_trashed: false });
        setFolders([]);
        setDocuments(dList.filter((d) => d.quality_flag === "needs_review"));
      } else if (currentView === "home" || currentView === "recent") {
        const dList = await api.documents.list({ include_all: true, is_trashed: false });
        setFolders([]);
        setDocuments(dList);
      } else {
        const [fList, dList] = await Promise.all([
          api.folders.list({ parent_id: validParentId, is_trashed: false }),
          api.documents.list({ folder_id: validParentId, is_trashed: false }),
        ]);
        setFolders(fList);
        setDocuments(dList);
      }
    } catch (err) {
      if (isAccessError(err)) {
        setFolders([]);
        setDocuments([]);
        setAccessDenied(true);
        return;
      }
      console.warn("Could not fetch backend drive items, falling back to local offline store:", err);
      const validParentId = isUUID(currentFolderId) ? currentFolderId : null;
      setDriveStats(offlineStore.getStats());
      setFolderTree(offlineStore.getFolderTree());
      setDocuments(offlineStore.getDocuments(validParentId));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!searchQuery) {
      loadContents();
    }
  }, [currentView, currentFolderId, searchQuery]);

  // Smart background polling while any document is pending/indexing embeddings
  useEffect(() => {
    const hasPendingDocs = documents.some((d) => d.status === "pending" || d.status === "processing");
    const hasIndexingUploads = uploads.some((u) => u.status === "indexing" || u.status === "uploading");

    if (!hasPendingDocs && !hasIndexingUploads) return;

    const interval = setInterval(async () => {
      try {
        const validParentId = isUUID(currentFolderId) ? currentFolderId : null;
        let latestDocs: DocumentListItem[] = [];
        if (currentView === "starred") {
          latestDocs = await api.documents.list({ is_starred: true, is_trashed: false });
        } else if (currentView === "trash") {
          latestDocs = await api.documents.list({ is_trashed: true });
        } else if (currentView === "home" || currentView === "recent") {
          latestDocs = await api.documents.list({ include_all: true, is_trashed: false });
        } else {
          latestDocs = await api.documents.list({ folder_id: validParentId, is_trashed: false });
        }

        setDocuments(latestDocs);

        // Update upload widget items based on real DB status
        setUploads((prev) =>
          prev.map((u) => {
            const matchDoc = latestDocs.find(
              (d) => (u.documentId && d.id === u.documentId) || d.title === u.name
            );
            if (matchDoc) {
              if (matchDoc.status === "indexed") {
                return { ...u, progress: 100, status: "completed" };
              } else if (matchDoc.status === "failed") {
                return { ...u, status: "error", errorMsg: "Indexing failed" };
              } else {
                return { ...u, progress: 100, status: "indexing" };
              }
            }
            return u;
          })
        );
      } catch (err) {
        console.warn("Background polling notice:", err);
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [documents, uploads, currentView, currentFolderId, searchQuery]);

  // Handlers
  const handleSearch = async (query: string, useAi: boolean) => {
    if (!isOnline) {
      setAiWarningFeature("Global AI Search & RAG");
      setShowAIWarningModal(true);
      return;
    }
    setSearchQuery(query);
    setSearching(true);
    try {
      // Progressive: render the documents the moment they arrive rather than
      // waiting for the grounded answer, which costs an extra LLM round-trip
      // and lands seconds later (much longer when that provider throttles).
      // The summary then fills in underneath. queryStream falls back to the
      // blocking endpoint by itself if SSE is unavailable.
      const res = await api.search.queryStream(
        query,
        {
          onResults: (results) => {
            setSearchResponse((prev) => ({
              ...(prev ?? {}),
              query,
              results,
              ai_summary: "",
              citations: [],
              summaryPending: true,
            } as any));
            setShowRightChatDrawer(true);
          },
          onSummary: (summary) => {
            setSearchResponse((prev) => ({
              ...(prev ?? {}),
              ...summary,
              summaryPending: false,
            } as any));
          },
        },
        5,
        null,
        rerankProvider,
        generateSummary
      );
      setSearchResponse({ ...(res as any), summaryPending: false });
      // AUTO-OPEN RIGHT-SIDE PERSISTENT CHAT JUST IN TIME ON SEARCH
      setShowRightChatDrawer(true);
    } catch (err) {
      console.error("Search failed:", err);
    } finally {
      setSearching(false);
    }
  };


  const handleClearSearch = () => {
    setSearchQuery("");
    setSearchResponse(null);
    setShowRightChatDrawer(false);
    loadContents();
  };

  const handleSelectFolder = (folder: Folder, isMulti?: boolean) => {
    // Same bug and same fix as handleSelectDoc above -- see its comment.
    if (isMulti) {
      setSelectedDocIds(new Set());
      setSelectedFolderIds((prev) => {
        const next = new Set(prev);
        if (next.has(folder.id)) next.delete(folder.id);
        else next.add(folder.id);
        return next;
      });
      return;
    }

    const isSoleSelection = selectedFolderIds.size === 1 && selectedFolderIds.has(folder.id) && selectedFolder?.id === folder.id;
    if (isSoleSelection) {
      setSelectedFolderIds(new Set());
      setSelectedFolder(null);
      setShowDetailPanel(false);
      return;
    }

    setSelectedDoc(null);
    setSelectedDocIds(new Set());
    setSelectedFolder(folder);
    setShowDetailPanel(true);
    setSelectedFolderIds(new Set([folder.id]));
  };

  const handleSelectDoc = (doc: DocumentListItem, isMulti?: boolean) => {
    // Real bug found live 2026-09-02: unchecking an already-selected row's
    // checkbox (or clicking an already-sole-selected row again) never
    // actually deselected it. The old logic always built `next` from a
    // FRESH empty set whenever isMulti was false, so `next.has(doc.id)`
    // was always false for a plain click -- it could only ever add, never
    // remove, no matter what was already selected. It also unconditionally
    // reopened the detail panel on every call, including checkbox clicks
    // that are meant to be pure bulk-selection with no detail-panel side
    // effect -- which is what looked like the row "shifting" (the panel
    // popping open/refocusing) on an action that should have just cleared
    // a checkbox.
    if (isMulti) {
      // Bulk multi-select (checkbox, ctrl/shift+click) -- toggles the
      // selection set only, never touches the single-doc detail panel.
      setSelectedFolderIds(new Set());
      setSelectedDocIds((prev) => {
        const next = new Set(prev);
        if (next.has(doc.id)) next.delete(doc.id);
        else next.add(doc.id);
        return next;
      });
      return;
    }

    // Plain click: single-select. Clicking the row that's already the
    // sole selection deselects it and closes the detail panel, instead of
    // silently re-adding it to an empty set (the actual bug).
    const isSoleSelection = selectedDocIds.size === 1 && selectedDocIds.has(doc.id) && selectedDoc?.id === doc.id;
    if (isSoleSelection) {
      setSelectedDocIds(new Set());
      setSelectedDoc(null);
      setShowDetailPanel(false);
      return;
    }

    setSelectedFolder(null);
    setSelectedFolderIds(new Set());
    setSelectedDoc(doc);
    setShowDetailPanel(true);
    setSelectedDocIds(new Set([doc.id]));
  };

  const handleToggleSelectAll = () => {
    const totalVisible = folders.length + documents.length;
    const totalSelected = selectedFolderIds.size + selectedDocIds.size;
    if (totalSelected >= totalVisible && totalVisible > 0) {
      setSelectedFolderIds(new Set());
      setSelectedDocIds(new Set());
    } else {
      setSelectedFolderIds(new Set(folders.map((f) => f.id)));
      setSelectedDocIds(new Set(documents.map((d) => d.id)));
    }
  };

  const handleBulkStar = async () => {
    for (const fId of Array.from(selectedFolderIds)) {
      await api.folders.toggleStar(fId).catch(() => {});
    }
    for (const dId of Array.from(selectedDocIds)) {
      await api.documents.toggleStar(dId).catch(() => {});
    }
    setSelectedFolderIds(new Set());
    setSelectedDocIds(new Set());
    loadContents();
  };

  const handleBulkDownload = () => {
    selectedDocIds.forEach((dId) => {
      const doc = documents.find((d) => d.id === dId);
      if (doc?.download_url) {
        const a = document.createElement("a");
        a.href = doc.download_url;
        a.download = doc.title;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      }
    });
  };

  const handleBulkTrash = async () => {
    const count = selectedFolderIds.size + selectedDocIds.size;
    if (currentView === "trash") {
      if (!canDeletePermanent) return;
      if (confirm(`Are you sure you want to permanently delete ${count} selected items?`)) {
        for (const fId of Array.from(selectedFolderIds)) {
          await api.folders.deletePermanent(fId).catch(() => {});
        }
        for (const dId of Array.from(selectedDocIds)) {
          await api.documents.deletePermanent(dId).catch(() => {});
        }
      }
    } else if (confirm(`Move ${count} selected item${count === 1 ? "" : "s"} to Bin?`)) {
      for (const fId of Array.from(selectedFolderIds)) {
        await api.folders.toggleTrash(fId).catch(() => {});
      }
      for (const dId of Array.from(selectedDocIds)) {
        await api.documents.toggleTrash(dId).catch(() => {});
      }
    } else {
      return;
    }
    setSelectedFolderIds(new Set());
    setSelectedDocIds(new Set());
    loadContents();
  };

  const handleOpenFolder = (folder: Folder) => {
    setCurrentView("my-drive");
    setCurrentFolderId(folder.id);
    setSelectedFolder(folder);
    setSelectedDoc(null);
    setSelectedFolderIds(new Set());
    setSelectedDocIds(new Set());
  };

  const handleSelectFolderId = (folderId: string) => {
    setCurrentView("my-drive");
    setCurrentFolderId(folderId);
    setSelectedDoc(null);
    setSelectedFolderIds(new Set());
    setSelectedDocIds(new Set());
    setShowRightChatDrawer(false);
  };

  const handleCreateFolder = async (name: string, color: string) => {
    const validParentId = isUUID(currentFolderId) ? currentFolderId : null;
    try {
      if (isOnline) {
        await api.folders.create(name, validParentId, color);
      } else {
        throw new Error("Offline Mode");
      }
    } catch (err: any) {
      offlineStore.addAction({
        type: "create_folder",
        payload: { name, parent_id: validParentId, color },
      });
      const mockFolder: Folder = {
        id: `off_f_${Date.now()}`,
        name,
        color,
        parent_id: validParentId,
        tenant_id: "local",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        is_starred: false,
        is_trashed: false,
      };
      setFolders((prev) => [mockFolder, ...prev]);
    }
    setIsNewFolderOpen(false);
    loadContents();
  };

  const handlePerformRename = async (newName: string) => {
    if (!itemToRename) return;
    try {
      if (isOnline && isUUID(itemToRename.item.id)) {
        if (itemToRename.type === "folder") {
          await api.folders.update(itemToRename.item.id, { name: newName });
        } else {
          await api.documents.update(itemToRename.item.id, { title: newName });
        }
      } else {
        throw new Error("Offline or local item");
      }
    } catch (err: any) {
      if (itemToRename.type === "folder") {
        offlineStore.addAction({
          type: "rename_folder",
          payload: { folder_id: itemToRename.item.id, new_name: newName },
        });
        setFolders((prev) => prev.map((f) => (f.id === itemToRename.item.id ? { ...f, name: newName } : f)));
      }
    }
    setItemToRename(null);
    loadContents();
  };

  const handlePerformMove = async (targetFolderId: string | null) => {
    const validTargetId = isUUID(targetFolderId) ? targetFolderId : null;
    // Real bug found live 2026-09-10 (QA report): every failure here —
    // including the backend's own "move to root silently no-ops" bug,
    // now fixed at the source — was swallowed with nothing shown to the
    // user, so the dialog closed looking like success while the item
    // never actually moved. Collecting failures and alerting (same
    // pattern already used for "Failed to empty Bin" below) beats a
    // silent no-op for a destructive-feeling, easy-to-not-notice action.
    const failures: string[] = [];
    if (itemToMove?.item?.id === "bulk" || selectedFolderIds.size > 0 || selectedDocIds.size > 0) {
      for (const fId of Array.from(selectedFolderIds)) {
        await api.folders.update(fId, { parent_id: validTargetId }).catch((err: any) => {
          failures.push(err?.message || `folder ${fId}`);
        });
      }
      for (const dId of Array.from(selectedDocIds)) {
        await api.documents.update(dId, { folder_id: validTargetId }).catch((err: any) => {
          failures.push(err?.message || `document ${dId}`);
        });
      }
      setSelectedFolderIds(new Set());
      setSelectedDocIds(new Set());
    }

    if (itemToMove && isUUID(itemToMove.item.id)) {
      try {
        if (itemToMove.type === "folder") {
          await api.folders.update(itemToMove.item.id, { parent_id: validTargetId });
        } else {
          await api.documents.update(itemToMove.item.id, { folder_id: validTargetId });
        }
      } catch (err: any) {
        failures.push(err?.message || itemToMove.item.id);
      }
    }
    setItemToMove(null);
    loadContents();
    if (failures.length > 0) {
      alert(`Move failed for ${failures.length} item(s):\n\n${failures.join("\n")}`);
    }
  };

  const handleRestoreItem = async (type: "folder" | "doc", id: string) => {
    if (!isUUID(id)) return;
    try {
      if (type === "folder") {
        await api.folders.toggleTrash(id);
      } else {
        await api.documents.toggleTrash(id);
      }
      loadContents();
    } catch (err) {
      console.warn("Restore failed:", err);
    }
  };

  const handlePermanentDelete = async (type: "folder" | "doc", id: string) => {
    if (!canDeletePermanent) return;
    if (!confirm("Are you sure you want to permanently delete this item?")) return;
    try {
      if (isOnline && isUUID(id)) {
        if (type === "folder") {
          await api.folders.deletePermanent(id);
        } else {
          await api.documents.deletePermanent(id);
        }
      } else {
        throw new Error("Offline");
      }
    } catch (err) {
      // A real server answer (403 wrong role, 404 gone, 409 retention…) is
      // not "offline" — queueing it for later sync and hiding the item
      // locally would pretend a refused delete succeeded.
      if (err instanceof ApiError) {
        alert(`Could not delete: ${err.message}`);
        loadContents();
        return;
      }
      if (type === "folder") {
        offlineStore.addAction({ type: "delete_folder", payload: { folder_id: id } });
        setFolders((prev) => prev.filter((f) => f.id !== id));
      } else {
        offlineStore.addAction({ type: "delete_document", payload: { doc_id: id } });
        setDocuments((prev) => prev.filter((d) => d.id !== id));
      }
    }
    loadContents();
  };

  const processFilesForUpload = async (files: File[]) => {
    if (files.length === 0) return;

    const validParentId = isUUID(currentFolderId) ? currentFolderId : null;

    const newUploadItems: UploadItem[] = files.map((f, i) => ({
      id: `${Date.now()}-${i}`,
      name: f.name,
      progress: 10,
      status: "uploading",
    }));

    setUploads((prev) => [...prev, ...newUploadItems]);

    try {
      if (files.length === 1) {
        const resp = await api.documents.upload(files[0], validParentId);
        setUploads((prev) =>
          prev.map((u) =>
            newUploadItems.some((n) => n.id === u.id)
              ? { ...u, documentId: resp.document_id, progress: 100, status: "indexing" }
              : u
          )
        );
      } else {
        const resp = await api.documents.uploadBulk(files, validParentId);
        setUploads((prev) =>
          prev.map((u) => {
            const matchingDoc = resp.documents?.find((d: any) => d.title === u.name);
            return newUploadItems.some((n) => n.id === u.id)
              ? { ...u, documentId: matchingDoc?.document_id, progress: 100, status: "indexing" }
              : u;
          })
        );
      }

      loadContents();
    } catch (err: any) {
      setUploads((prev) =>
        prev.map((u) =>
          newUploadItems.some((n) => n.id === u.id)
            ? { ...u, status: "error", errorMsg: err.message || "Upload failed" }
            : u
        )
      );
    }
  };

  // Recursively read a dropped FileSystemEntry (file or directory) into a
  // flat list of {file, relativePath}. relativePath includes the dropped
  // folder's own name as its first segment, so a file dropped inside
  // "Kunal 2/subdir/x.py" comes back as relativePath "Kunal 2/subdir/x.py"
  // with file.name cleanly just "x.py".
  const readEntryContents = (entry: any, basePath: string): Promise<{ file: File; relativePath: string }[]> => {
    return new Promise((resolve) => {
      if (entry.isFile) {
        entry.file(
          (file: File) => resolve([{ file, relativePath: basePath ? `${basePath}/${entry.name}` : entry.name }]),
          () => resolve([])
        );
      } else if (entry.isDirectory) {
        const dirReader = entry.createReader();
        const collected: any[] = [];
        const readBatch = () => {
          dirReader.readEntries(async (batch: any[]) => {
            if (batch.length === 0) {
              const nextBase = basePath ? `${basePath}/${entry.name}` : entry.name;
              const nested = await Promise.all(collected.map((child) => readEntryContents(child, nextBase)));
              resolve(nested.flat());
            } else {
              collected.push(...batch);
              readBatch();
            }
          }, () => resolve([]));
        };
        readBatch();
      } else {
        resolve([]);
      }
    });
  };

  // Cache of "path/segments/joined" -> created/found folder id, so dropping
  // the same folder twice (or a tree with many files sharing subfolders)
  // doesn't create duplicate folders or refetch on every file.
  const folderPathCache = useRef<Map<string, string>>(new Map());

  const getOrCreateFolderId = async (segments: string[], baseParentId: string | null): Promise<string | null> => {
    let parentId: string | null = baseParentId;
    let pathKey = "";
    for (const seg of segments) {
      pathKey = pathKey ? `${pathKey}/${seg}` : seg;
      const cached = folderPathCache.current.get(pathKey);
      if (cached) {
        parentId = cached;
        continue;
      }
      let folderId: string | null = null;
      try {
        const existing = await api.folders.list({ parent_id: parentId });
        const match = existing.find((f) => f.name === seg);
        if (match) folderId = match.id;
      } catch (_) {
        // listing failed; fall through to create
      }
      if (!folderId) {
        const created = await api.folders.create(seg, parentId);
        folderId = created.id;
      }
      folderPathCache.current.set(pathKey, folderId);
      parentId = folderId;
    }
    return parentId;
  };

  // Upload a folder tree: creates real, nested folders matching the dropped
  // directory structure, and uploads each file into the correct folder with
  // just its own filename (never "FolderName/file.ext" baked into the title).
  const processFolderUpload = async (entries: { file: File; relativePath: string }[]) => {
    if (entries.length === 0) return;

    const baseParentId = isUUID(currentFolderId) ? currentFolderId : null;

    const newUploadItems: UploadItem[] = entries.map((entry, i) => ({
      id: `${Date.now()}-${i}`,
      name: entry.relativePath,
      progress: 10,
      status: "uploading",
    }));
    setUploads((prev) => [...prev, ...newUploadItems]);

    for (let i = 0; i < entries.length; i++) {
      const { file, relativePath } = entries[i];
      const uploadItemId = newUploadItems[i].id;
      const segments = relativePath.split("/");
      const fileName = segments.pop() as string;

      try {
        const folderId = await getOrCreateFolderId(segments, baseParentId);
        const cleanFile = new File([file], fileName, { type: file.type });
        const resp = await api.documents.upload(cleanFile, folderId);
        setUploads((prev) =>
          prev.map((u) =>
            u.id === uploadItemId
              ? { ...u, documentId: resp.document_id, progress: 100, status: "indexing" }
              : u
          )
        );
      } catch (err: any) {
        setUploads((prev) =>
          prev.map((u) =>
            u.id === uploadItemId
              ? { ...u, status: "error", errorMsg: err.message || "Upload failed" }
              : u
          )
        );
      }
    }

    loadContents();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    const hasRelativePaths = files.some((f) => (f as any).webkitRelativePath);
    if (hasRelativePaths) {
      const entries = files.map((f) => ({ file: f, relativePath: (f as any).webkitRelativePath || f.name }));
      await processFolderUpload(entries);
      if (fileInputRef.current) fileInputRef.current.value = "";
      if (folderInputRef.current) folderInputRef.current.value = "";
      return;
    }
    if (files.length > 0) {
      await processFilesForUpload(files);
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (folderInputRef.current) folderInputRef.current.value = "";
  };


  // Preview Document from Search Result
  const handlePreviewSearchResult = (res: SearchResult) => {
    const searchDocItem: DocumentListItem = {
      id: res.document_id,
      title: res.document_name || "Search Result Document",
      status: "indexed",
      created_at: new Date().toISOString(),
      file_size_bytes: 1024,
      is_starred: false,
      is_trashed: false,
      download_url: res.download_url,
    };
    setPreviewDoc(searchDocItem);
  };

  // What the right-hand info panel should describe. selectedDoc/selectedFolder
  // only ever track PLAIN clicks -- the checkbox path is deliberately
  // side-effect-free (see handleSelectDoc) -- so on their own they go stale the
  // moment a checkbox is involved: tick one box and the panel still described
  // whichever row was clicked earlier, which might not even be selected any
  // more. Derive the subject from the selection itself, and show nothing while
  // several rows are selected, since the panel can only ever describe one.
  const totalSelected = selectedDocIds.size + selectedFolderIds.size;
  const detailDoc =
    selectedDocIds.size === 1
      ? documents.find((d) => selectedDocIds.has(d.id)) ?? selectedDoc
      : totalSelected === 0
      ? selectedDoc
      : null;
  const detailFolder =
    selectedFolderIds.size === 1
      ? folders.find((f) => selectedFolderIds.has(f.id)) ?? selectedFolder
      : totalSelected === 0
      ? selectedFolder
      : null;

  return (
    <div className="flex flex-col h-screen w-screen bg-gdriveBg overflow-hidden select-none">
      {/* Hidden File & Folder Inputs */}
      <input
        type="file"
        multiple
        ref={fileInputRef}
        onChange={handleFileChange}
        className="hidden"
      />
      <input
        type="file"
        multiple
        ref={folderInputRef}
        onChange={handleFileChange}
        className="hidden"
        {...({ webkitdirectory: "", directory: "" } as any)}
      />

      {/* Offline Status & Sync Banner */}
      <OfflineBanner />

      {/* Top Header */}
      <DriveTopHeader
        onSearch={handleSearch}
        searchQuery={searchQuery}
        onClearSearch={handleClearSearch}
        showInfoPanel={showDetailPanel}
        onToggleInfoPanel={() => setShowDetailPanel(!showDetailPanel)}
        rerankProvider={rerankProvider}
        onChangeRerankProvider={handleSetRerankProvider}
        generateSummary={generateSummary}
        onChangeGenerateSummary={handleSetGenerateSummary}
        onNavigateHome={() => {
          setSearchQuery("");
          setSearchResponse(null);
          setShowRightChatDrawer(false);
          setCurrentView("home");
          setCurrentFolderId(null);
          setSelectedFolder(null);
          setSelectedDoc(null);
        }}
      />

      {/* Selection action bar — appears whenever any file/folder is checked
          or ctrl/shift-clicked. Previously the only way to bulk-act was an
          undiscoverable right-click context menu with no visible checkbox
          UI anywhere (found live-testing this session). */}
      {(selectedDocIds.size + selectedFolderIds.size) > 0 && (
        <div className="flex items-center justify-between px-4 py-2 bg-[#e8f0fe] border-b border-[#c2e7ff]">
          <div className="flex items-center gap-3">
            <button
              onClick={() => {
                setSelectedFolderIds(new Set());
                setSelectedDocIds(new Set());
              }}
              className="p-1.5 rounded-full hover:bg-[#c2e7ff] text-[#001d35] transition-colors"
              aria-label="Clear selection"
            >
              <X className="w-4 h-4" />
            </button>
            <span className="text-sm font-semibold text-[#001d35]">
              {selectedDocIds.size + selectedFolderIds.size} selected
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={handleBulkStar}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold text-[#444746] bg-white border border-[#e1e3e1] hover:bg-[#f0f4f9] transition-colors"
            >
              <Star className="w-3.5 h-3.5" />
              Star
            </button>
            {selectedDocIds.size > 0 && (
              <button
                onClick={handleBulkDownload}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold text-[#444746] bg-white border border-[#e1e3e1] hover:bg-[#f0f4f9] transition-colors"
              >
                <Download className="w-3.5 h-3.5" />
                Download
              </button>
            )}
            <button
              onClick={() => setItemToMove({ type: "doc", item: { id: "bulk" } as any })}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold text-[#444746] bg-white border border-[#e1e3e1] hover:bg-[#f0f4f9] transition-colors"
            >
              <FolderInput className="w-3.5 h-3.5" />
              Move
            </button>
            {(currentView !== "trash" || canDeletePermanent) && (
            <button
              onClick={handleBulkTrash}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold text-red-600 bg-white border border-red-200 hover:bg-red-50 transition-colors"
            >
              <Trash2 className="w-3.5 h-3.5" />
              {currentView === "trash" ? "Delete permanently" : "Move to Bin"}
            </button>
            )}
          </div>
        </div>
      )}

      {/* Body Area */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Navigation Sidebar with Tree */}
        <DriveSidebar
          currentView={currentView}
          onSelectView={(v) => {
            if (v === "chat" && !isOnline) {
              setAiWarningFeature("AI Assistant Chat");
              setShowAIWarningModal(true);
              return;
            }
            setSearchQuery("");
            setShowRightChatDrawer(false);
            setCurrentView(v);
            setCurrentFolderId(null);
            setSelectedFolder(null);
            setSelectedDoc(null);
          }}
          onOpenNewFolderModal={() => setIsNewFolderOpen(true)}
          onTriggerFileUpload={() => fileInputRef.current?.click()}
          onOpenConnectorModal={() => setIsConnectorModalOpen(true)}
          stats={driveStats}
          folderTree={folderTree}
          activeFolderId={currentFolderId}
          onSelectFolder={handleSelectFolderId}
          onSelectDoc={(d) => handleSelectDoc(d)}
          onPreviewDoc={(d) => setPreviewDoc(d)}
        />

        {/* Center Main Dashboard Canvas */}
        <main
          onContextMenu={handleContextMenu}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`flex-1 bg-white rounded-3xl my-2 ml-1 mr-2 flex flex-col border border-[#e1e3e1] shadow-sm relative ${
            currentView === "chat" ? "p-0 overflow-hidden" : "p-6 overflow-y-auto"
          }`}
        >
          {/* Drag and Drop Overlay Indicator */}
          {isDragging && (
            <div className="absolute inset-0 bg-[#0d2e5c]/10 backdrop-blur-md rounded-3xl border-2 border-dashed border-[#0d2e5c] z-40 flex flex-col items-center justify-center p-8 text-center animate-fadeIn pointer-events-none">
              <div className="w-20 h-20 bg-[#0d2e5c] text-white rounded-full flex items-center justify-center shadow-xl shadow-[#0d2e5c]/30 mb-4 animate-bounce">
                <UploadCloud className="w-10 h-10" />
              </div>
              <h3 className="text-xl font-bold text-[#001d35] mb-1">Drop files here to upload to DMS</h3>
              <p className="text-sm text-[#444746] max-w-md">
                Upload single or multiple documents directly into your current directory folder.
              </p>
            </div>
          )}


          {/* Top Breadcrumb & Canvas Header (hidden in dedicated chat mode) */}
          {currentView !== "chat" && (
            <div className="flex items-center justify-between mb-4 border-b border-[#e1e3e1] pb-3">
              <DriveBreadcrumbs
                currentView={currentView}
                currentFolder={currentFolder}
                folderPath={folderPath}
                onNavigateRoot={() => {
                  setCurrentFolderId(null);
                  setCurrentFolder(null);
                  setFolderPath([]);
                }}
                onNavigateFolder={handleSelectFolderId}
              />
            </div>
          )}

          {/* Active Chat Mode */}

          {currentView === "chat" ? (
            <div className="flex-1 flex overflow-hidden h-full">
              <PersistentChatPanel onPreviewDocument={(doc) => setPreviewDoc(doc)} />
            </div>
          ) : searchQuery ? (
            <div className="flex-1 space-y-6">
              {searching ? (
                <div className="text-center py-12 text-sm text-[#444746] animate-pulse">
                  Searching your Drive with AI RAG...
                </div>
              ) : searchResponse ? (
                <div className="space-y-6">
                  {searchResponse.ai_summary && (
                    <AISummary summary={searchResponse.ai_summary} citations={searchResponse.citations} />
                  )}

                  <div className="flex items-center justify-between border-b border-[#e1e3e1] pb-2">
                    <h3 className="text-base font-semibold text-[#1f1f1f]">Search Results</h3>
                    <div className="flex items-center gap-3">
                      <button
                        onClick={() => setShowRightChatDrawer(!showRightChatDrawer)}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-[#0d2e5c] hover:bg-[#0945a5] text-white rounded-full text-xs font-semibold shadow-sm transition-all"
                      >
                        <Sparkles className="w-3.5 h-3.5" />
                        <span>{showRightChatDrawer ? "Hide AI Assistant" : "AI Persistent Chatbot"}</span>
                      </button>
                      <span className="text-xs text-[#444746]">
                        {searchResponse.results.length} matches ({searchResponse.took_ms}ms)
                      </span>
                    </div>
                  </div>

                  {searchResponse.reranked === false && (
                    <div className="p-3 bg-amber-50 border border-amber-200 rounded-2xl text-xs text-amber-800 flex items-center gap-2">
                      <Sparkles className="w-4 h-4 shrink-0" />
                      <span>AI ranking temporarily unavailable — showing unranked results by keyword/vector match only.</span>
                    </div>
                  )}

                  {searchResponse.results.length > 0 ? (
                    <div className="grid grid-cols-1 gap-4">
                      {searchResponse.results.map((res, idx) => (
                        <ResultCard key={`${res.document_id}-${idx}`} result={res} onPreview={handlePreviewSearchResult} reranked={searchResponse.reranked} grounded={searchResponse.grounded} />
                      ))}

                      {/* Small technology message at bottom of loaded docs if results > 1 */}
                      {searchResponse.results.length > 1 && (
                        <div className="p-3 bg-[#edf2fc]/60 border border-[#c4c7c5]/50 rounded-2xl flex items-center justify-between text-xs text-[#444746] mt-2 shadow-2xs">
                          <div className="flex items-center gap-2 font-medium">
                            <Sparkles className="w-4 h-4 text-[#0d2e5c]" />
                            <span>Search Technology Used:</span>
                            <span className="font-bold text-[#0d2e5c] px-2.5 py-0.5 rounded-full bg-white border border-[#0d2e5c]/20 shadow-2xs">
                              {searchResponse.search_mode === "HyDE"
                                ? "HyDE"
                                : searchResponse.search_mode || "vector+keyword"}
                            </span>
                          </div>
                          <span className="text-[11px] text-[#747775] font-medium">
                            Retrieved {searchResponse.results.length} documents in {searchResponse.took_ms}ms
                          </span>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="text-center py-16 bg-[#f8f9fa] rounded-2xl border border-[#e1e3e1]">
                      <FolderSearch className="w-12 h-12 text-[#444746] mx-auto mb-3 opacity-50" />
                      <p className="text-[#1f1f1f] font-semibold text-sm">No search results found</p>
                      <p className="text-xs text-[#444746] mt-1">Try another search term or query.</p>
                    </div>
                  )}
                </div>
              ) : null}
            </div>
          ) : accessDenied ? (
            <div className="flex-1 flex items-start justify-center pt-10">
              <NoAccess message="This folder isn't shared with your department. Ask an IT admin to grant your department access if you need it.">
                <button
                  onClick={() => {
                    setCurrentFolderId(null);
                    setCurrentFolder(null);
                    setFolderPath([]);
                    setCurrentView("my-drive");
                  }}
                  className="mt-2 px-4 py-1.5 rounded-full text-xs font-semibold bg-white border border-amber-300 text-amber-900 hover:bg-amber-100"
                >
                  Back to My Drive
                </button>
              </NoAccess>
            </div>
          ) : currentView === "trash" ? (
            /* Trash View with Restore, Permanent Delete & 30-Day Auto-Delete Banner */
            <div className="flex-1 space-y-6">
              <div className="flex items-center justify-between border-b border-[#e1e3e1] pb-3">
                <div>
                  <h2 className="text-xl font-semibold text-[#1f1f1f]">Items in Bin</h2>
                  <p className="text-xs text-[#747775] mt-1 font-medium flex items-center gap-1.5">
                    <Clock className="w-3.5 h-3.5 text-amber-600" />
                    <span>Folders are permanently deleted after 30 days. Documents follow their own retention policy — some are never auto-deleted.</span>
                  </p>
                </div>

                {(folders.length > 0 || documents.length > 0) && roleCan("content.deletePermanent") && (
                  <button
                    onClick={async () => {
                      if (confirm("Are you sure you want to empty the Bin? Items not protected by retention policy will be permanently deleted immediately.")) {
                        try {
                          const result = await api.documents.cleanupTrash(0);
                          loadContents();

                          const protectedCount = result?.protected_documents?.length || 0;
                          const pendingCount = result?.pending_documents?.length || 0;
                          const deletedCount = (result?.deleted_documents || 0) + (result?.deleted_folders || 0);

                          if (protectedCount > 0 || pendingCount > 0) {
                            const lines = [`${deletedCount} item(s) permanently deleted.`];
                            if (protectedCount > 0) {
                              lines.push(
                                `${protectedCount} item(s) could not be deleted — protected by retention policy (never auto-purged): ` +
                                  result.protected_documents.map((d: any) => `"${d.title}"`).join(", ")
                              );
                            }
                            if (pendingCount > 0) {
                              lines.push(
                                `${pendingCount} item(s) not yet eligible for deletion: ` +
                                  result.pending_documents.map((d: any) => `"${d.title}" (${d.days_remaining}d remaining)`).join(", ")
                              );
                            }
                            alert(lines.join("\n\n"));
                          } else if (deletedCount === 0) {
                            alert("Nothing was deleted — the Bin may already be empty.");
                          }
                        } catch (err: any) {
                          alert("Failed to empty Bin: " + (err.message || "Unknown error"));
                        }
                      }
                    }}
                    className="px-4 py-2 text-xs font-semibold text-red-600 hover:bg-red-50 border border-red-200 rounded-full transition-colors shadow-xs"
                  >
                    Empty Bin
                  </button>
                )}
              </div>

              {folders.length === 0 && documents.length === 0 ? (
                <div className="text-center py-20 bg-[#f8f9fa] rounded-3xl border border-[#e1e3e1]">
                  <Trash2 className="w-12 h-12 text-[#747775] mx-auto mb-3 opacity-40" />
                  <h3 className="text-sm font-semibold text-[#1f1f1f]">Bin is empty</h3>
                  <p className="text-xs text-[#747775] mt-1">Items moved to the Bin will appear here for 30 days before permanent deletion.</p>
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
                  {folders.map((f) => {
                    const { text: daysText, daysLeft } = getDaysRemainingInBin(f.trashed_at);
                    const isSelected = selectedFolderIds.has(f.id);
                    return (
                      <div
                        key={f.id}
                        role="button"
                        tabIndex={0}
                        onClick={(e) => handleSelectFolder(f, e.ctrlKey || e.metaKey || e.shiftKey)}
                        onKeyDown={onKeyActivate(() => handleSelectFolder(f, false))}
                        className={`p-4 rounded-2xl flex items-center justify-between shadow-2xs cursor-pointer select-none border transition-all ${
                          isSelected ? "bg-[#c2e7ff] border-[#0d2e5c]" : "bg-[#f8f9fa] border-[#e1e3e1]"
                        }`}
                      >
                        <div className="min-w-0 flex-1 pr-2">
                          <span className="font-semibold text-sm text-[#1f1f1f] truncate block">{f.name}</span>
                          <div className="flex items-center gap-2 mt-1">
                            <span className="text-[10px] text-[#747775] font-medium">Folder</span>
                            <span className={`inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
                              daysLeft <= 5 
                                ? "bg-red-50 text-red-700 border-red-200" 
                                : "bg-amber-50 text-amber-700 border-amber-200/80"
                            }`}>
                              <Clock className="w-3 h-3" />
                              {daysText}
                            </span>
                          </div>
                        </div>
                        <div className="flex items-center gap-1 flex-shrink-0">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRestoreItem("folder", f.id);
                            }}
                            className="p-1.5 text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
                            title="Restore folder"
                          >
                            <RotateCcw className="w-4 h-4" />
                          </button>
                          {canDeletePermanent && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handlePermanentDelete("folder", f.id);
                              }}
                              className="p-1.5 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                              title="Delete permanently"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}

                  {documents.map((d) => {
                    const { text: daysText, daysLeft } = getDaysRemainingInBin(d.trashed_at);
                    const isSelected = selectedDocIds.has(d.id);
                    return (
                      <div
                        key={d.id}
                        role="button"
                        tabIndex={0}
                        onClick={(e) => {
                          const isMulti = e.ctrlKey || e.metaKey || e.shiftKey;
                          if (isMulti) {
                            handleSelectDoc(d, true);
                            return;
                          }
                          handleTrashRowClick(d.id, () => handleSelectDoc(d, false));
                        }}
                        onKeyDown={onKeyActivate(() => setPreviewDoc(d))}
                        onDoubleClick={() => handleTrashRowDoubleClick(d.id, () => setPreviewDoc(d))}
                        className={`p-4 rounded-2xl flex items-center justify-between shadow-2xs cursor-pointer select-none border transition-all ${
                          isSelected ? "bg-[#c2e7ff] border-[#0d2e5c]" : "bg-[#f8f9fa] border-[#e1e3e1]"
                        }`}
                      >
                        <div className="min-w-0 flex-1 pr-2">
                          <span className="font-semibold text-sm text-[#1f1f1f] truncate block">{d.title}</span>
                          <div className="flex items-center gap-2 mt-1">
                            <span className="text-[10px] text-[#747775] font-medium">Document</span>
                            <span className={`inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
                              daysLeft <= 5 
                                ? "bg-red-50 text-red-700 border-red-200" 
                                : "bg-amber-50 text-amber-700 border-amber-200/80"
                            }`}>
                              <Clock className="w-3 h-3" />
                              {daysText}
                            </span>
                          </div>
                        </div>
                        <div className="flex items-center gap-1 flex-shrink-0">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRestoreItem("doc", d.id);
                            }}
                            className="p-1.5 text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
                            title="Restore document"
                          >
                            <RotateCcw className="w-4 h-4" />
                          </button>
                          {canDeletePermanent && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handlePermanentDelete("doc", d.id);
                              }}
                              className="p-1.5 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                              title="Delete permanently"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ) : currentView === "home" ? (
            /* Home View - Shows Only Recent Files Open */
            <div className="flex-1 space-y-4">
              <SuggestedFilesTable
                documents={documents}
                onSelectDoc={handleSelectDoc}
                onPreviewDoc={(d) => setPreviewDoc(d)}
                onContextMenu={(e, d) => handleItemContextMenu(e, "doc", d)}
                selectedDocId={selectedDoc?.id}
                selectedDocIds={selectedDocIds}
                onToggleSelectAll={handleToggleSelectAll}
                isAllSelected={documents.length > 0 && selectedDocIds.size === documents.length}
                onToggleStar={async (d) => {
                  if (isUUID(d.id)) {
                    await api.documents.toggleStar(d.id).catch(() => {});
                  }
                  loadContents();
                }}
                onRename={(d) => setItemToRename({ type: "doc", item: d })}
                onMove={(d) => setItemToMove({ type: "doc", item: d })}
                onTrash={async (d) => {
                  if (!confirm(`Move "${d.title}" to Bin?`)) return;
                  if (isUUID(d.id)) {
                    await api.documents.toggleTrash(d.id).catch(() => {});
                  }
                  loadContents();
                }}
              />
            </div>
          ) : (
            /* My Drive View & Subfolder Navigation */
            <div className="flex-1 space-y-6">
              {/* Suggested Folders Carousel */}
              <SuggestedFoldersSection
                folders={folders}
                onOpenFolder={handleOpenFolder}
                onSelectFolder={handleSelectFolder}
                onContextMenu={(e, f) => handleItemContextMenu(e, "folder", f)}
                selectedFolderIds={selectedFolderIds}
                onToggleStar={async (f) => {
                  if (isUUID(f.id)) {
                    await api.folders.toggleStar(f.id).catch(() => {});
                  }
                  loadContents();
                }}
                onRename={(f) => setItemToRename({ type: "folder", item: f })}
                onMove={(f) => setItemToMove({ type: "folder", item: f })}
                onTrash={async (f) => {
                  if (!confirm(`Move "${f.name}" to Bin?`)) return;
                  if (isUUID(f.id)) {
                    await api.folders.toggleTrash(f.id).catch(() => {});
                  }
                  loadContents();
                }}
              />

              <SuggestedFilesTable
                documents={documents}
                emptyType={currentView === "starred" ? "starred" : "default"}
                onSelectDoc={handleSelectDoc}
                onPreviewDoc={(d) => setPreviewDoc(d)}
                onContextMenu={(e, d) => handleItemContextMenu(e, "doc", d)}
                selectedDocId={selectedDoc?.id}
                selectedDocIds={selectedDocIds}
                onToggleSelectAll={handleToggleSelectAll}
                isAllSelected={documents.length > 0 && selectedDocIds.size === documents.length}
                onToggleStar={async (d) => {
                  if (isUUID(d.id)) {
                    await api.documents.toggleStar(d.id).catch(() => {});
                  }
                  loadContents();
                }}
                onRename={(d) => setItemToRename({ type: "doc", item: d })}
                onMove={(d) => setItemToMove({ type: "doc", item: d })}
                onTrash={async (d) => {
                  if (!confirm(`Move "${d.title}" to Bin?`)) return;
                  if (isUUID(d.id)) {
                    await api.documents.toggleTrash(d.id).catch(() => {});
                  }
                  loadContents();
                }}
              />
            </div>
          )}
        </main>

        {/* Right Info Drawer -- suppressed (not closed) while several rows are
            selected, so dropping back to one selection restores it. */}
        {showDetailPanel && totalSelected <= 1 && (
          <DriveDetailPanel
            selectedFolder={detailFolder}
            selectedDoc={detailDoc}
            onClose={() => setShowDetailPanel(false)}
          />
        )}

        {/* Right-Side Persistent Chat Drawer */}
        <RightSideChatDrawer
          isOpen={showRightChatDrawer}
          onClose={() => setShowRightChatDrawer(false)}
          initialQuery={searchQuery}
          initialResults={searchResponse?.results}
          onPreviewDocument={(doc) => setPreviewDoc(doc)}
        />

        {/* Far Right Apps Dock */}
        <RightDock />
      </div>

      {/* Universal Multi-Format Document Viewer Modal */}
      <DocumentPreviewModal
        isOpen={!!previewDoc}
        doc={previewDoc}
        onClose={() => setPreviewDoc(null)}
        onToggleStar={async (d) => {
          if (isUUID(d.id)) {
            await api.documents.toggleStar(d.id).catch(() => {});
            loadContents();
          }
        }}
      />

      {/* Modals */}
      <NewFolderModal
        isOpen={isNewFolderOpen}
        onClose={() => setIsNewFolderOpen(false)}
        onCreate={handleCreateFolder}
      />

      <ConnectorModal
        isOpen={isConnectorModalOpen}
        onClose={() => setIsConnectorModalOpen(false)}
      />

      <RenameModal
        isOpen={!!itemToRename}
        currentName={
          itemToRename
            ? "name" in itemToRename.item
              ? itemToRename.item.name
              : itemToRename.item.title
            : ""
        }
        onClose={() => setItemToRename(null)}
        onRename={handlePerformRename}
      />

      <MoveModal
        isOpen={!!itemToMove}
        onClose={() => setItemToMove(null)}
        onMove={handlePerformMove}
      />

      {/* Online-Only AI Feature Warning Modal */}
      <OnlineWarningModal
        isOpen={showAIWarningModal}
        featureName={aiWarningFeature}
        onClose={() => setShowAIWarningModal(false)}
      />

      {/* Upload Tracker Widget */}
      <UploadWidget uploads={uploads} onDismiss={() => setUploads([])} />

      {/* Right-Click Context Menu */}
      {contextMenu.visible && (
        <div
          role="presentation"
          className="fixed inset-0 z-50 pointer-events-auto"
          onClick={() => setContextMenu((prev) => ({ ...prev, visible: false }))}
          onContextMenu={(e) => {
            e.preventDefault();
            setContextMenu({ visible: true, x: e.clientX, y: e.clientY });
          }}
        >
          <div
            role="presentation"
            className="absolute w-56 bg-surface border border-borderDark rounded-2xl shadow-2xl py-2 text-textMain animate-fadeIn"
            style={{
              top: `${Math.min(contextMenu.y, typeof window !== "undefined" ? window.innerHeight - 160 : contextMenu.y)}px`,
              left: `${Math.min(contextMenu.x, typeof window !== "undefined" ? window.innerWidth - 240 : contextMenu.x)}px`,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => {
                setContextMenu((prev) => ({ ...prev, visible: false }));
                setIsNewFolderOpen(true);
              }}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-sm font-medium text-textMain hover:bg-white/10 transition-colors text-left"
            >
              <FolderPlus className="w-4 h-4 text-primary" />
              <span>Create new folder</span>
            </button>
            <div className="my-1 border-t border-borderDark/60" />
            <button
              onClick={() => {
                setContextMenu((prev) => ({ ...prev, visible: false }));
                fileInputRef.current?.click();
              }}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-sm font-medium text-textMain hover:bg-white/10 transition-colors text-left"
            >
              <Upload className="w-4 h-4 text-blue-400" />
              <span>Upload file</span>
            </button>
            <button
              onClick={() => {
                setContextMenu((prev) => ({ ...prev, visible: false }));
                folderInputRef.current?.click();
              }}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-sm font-medium text-textMain hover:bg-white/10 transition-colors text-left"
            >
              <FolderUp className="w-4 h-4 text-amber-400" />
              <span>Folder upload</span>
            </button>
          </div>
        </div>
      )}

      {/* Right-Click Item Context Menu */}
      {itemContextMenu?.isOpen && (() => {
        const totalSelected = selectedFolderIds.size + selectedDocIds.size;
        const isMulti = totalSelected > 1;

        return (
          <>
            <div
              role="presentation"
              className="fixed inset-0 z-50"
              onClick={() => setItemContextMenu(null)}
              onContextMenu={(e) => {
                e.preventDefault();
                setItemContextMenu(null);
              }}
            />
            <div
              style={{ top: `${itemContextMenu.y}px`, left: `${itemContextMenu.x}px` }}
              className="fixed z-50 w-56 bg-white rounded-2xl shadow-2xl border border-[#e1e3e1] p-1.5 text-xs text-[#1f1f1f] animate-fadeIn select-none"
            >
              {!isMulti && (
                <button
                  onClick={() => {
                    setItemContextMenu(null);
                    if (itemContextMenu.type === "folder") {
                      handleOpenFolder(itemContextMenu.item as Folder);
                    } else {
                      setPreviewDoc(itemContextMenu.item as DocumentListItem);
                    }
                  }}
                  className="flex items-center gap-2.5 w-full px-3 py-2 rounded-xl hover:bg-[#f0f4f9] font-medium"
                >
                  <Eye className="w-4 h-4 text-[#0d2e5c]" />
                  <span>{itemContextMenu.type === "folder" ? "Open Folder" : "Preview"}</span>
                </button>
              )}

              {/* Download */}
              {(isMulti ? selectedDocIds.size > 0 : itemContextMenu.type === "doc" && (itemContextMenu.item as DocumentListItem).download_url) && (
                <button
                  onClick={() => {
                    setItemContextMenu(null);
                    if (isMulti) {
                      handleBulkDownload();
                    } else if (itemContextMenu.type === "doc" && (itemContextMenu.item as DocumentListItem).download_url) {
                      const a = document.createElement("a");
                      a.href = (itemContextMenu.item as DocumentListItem).download_url || "#";
                      a.download = (itemContextMenu.item as DocumentListItem).title;
                      document.body.appendChild(a);
                      a.click();
                      document.body.removeChild(a);
                    }
                  }}
                  className="flex items-center gap-2.5 w-full px-3 py-2 rounded-xl hover:bg-[#f0f4f9] font-medium"
                >
                  <Download className="w-4 h-4 text-[#00639b]" />
                  <span>{isMulti ? `Download (${selectedDocIds.size} files)` : "Download"}</span>
                </button>
              )}

              {/* Rename (Single item only) */}
              {!isMulti && (
                <button
                  onClick={() => {
                    setItemContextMenu(null);
                    setItemToRename({ type: itemContextMenu.type, item: itemContextMenu.item });
                  }}
                  className="flex items-center gap-2.5 w-full px-3 py-2 rounded-xl hover:bg-[#f0f4f9] font-medium"
                >
                  <Edit2 className="w-4 h-4 text-[#444746]" />
                  <span>Rename</span>
                </button>
              )}

              {/* Move */}
              {currentView !== "trash" && (
                <button
                  onClick={() => {
                    setItemContextMenu(null);
                    if (isMulti) {
                      setItemToMove({ type: "doc", item: { id: "bulk" } as any });
                    } else {
                      setItemToMove({ type: itemContextMenu.type, item: itemContextMenu.item });
                    }
                  }}
                  className="flex items-center gap-2.5 w-full px-3 py-2 rounded-xl hover:bg-[#f0f4f9] font-medium"
                >
                  <FolderInput className="w-4 h-4 text-[#444746]" />
                  <span>{isMulti ? `Move (${totalSelected} items)` : "Move"}</span>
                </button>
              )}

              {/* Star */}
              <button
                onClick={async () => {
                  setItemContextMenu(null);
                  if (isMulti) {
                    await handleBulkStar();
                  } else {
                    if (itemContextMenu.type === "folder") {
                      await api.folders.toggleStar(itemContextMenu.item.id).catch(() => {});
                    } else {
                      await api.documents.toggleStar(itemContextMenu.item.id).catch(() => {});
                    }
                    loadContents();
                  }
                }}
                className="flex items-center gap-2.5 w-full px-3 py-2 rounded-xl hover:bg-[#f0f4f9] font-medium"
              >
                <Star className="w-4 h-4 text-amber-500" />
                <span>{isMulti ? `Star (${totalSelected} items)` : itemContextMenu.item.is_starred ? "Unstar" : "Star"}</span>
              </button>

              <div className="h-px bg-[#e1e3e1] my-1" />

              {/* Trash / Delete — permanent delete (Bin view) is role-gated */}
              {(currentView !== "trash" || canDeletePermanent) && (
              <button
                onClick={async () => {
                  setItemContextMenu(null);
                  if (isMulti) {
                    await handleBulkTrash();
                  } else {
                    if (currentView === "trash") {
                      handlePermanentDelete(itemContextMenu.type, itemContextMenu.item.id);
                    } else {
                      const itemName =
                        itemContextMenu.type === "folder"
                          ? (itemContextMenu.item as Folder).name
                          : (itemContextMenu.item as DocumentListItem).title;
                      if (!confirm(`Move "${itemName}" to Bin?`)) return;
                      if (itemContextMenu.type === "folder") {
                        await api.folders.toggleTrash(itemContextMenu.item.id).catch(() => {});
                      } else {
                        await api.documents.toggleTrash(itemContextMenu.item.id).catch(() => {});
                      }
                      loadContents();
                    }
                  }
                }}
                className="flex items-center gap-2.5 w-full px-3 py-2 rounded-xl hover:bg-red-50 text-red-600 font-medium"
              >
                <Trash2 className="w-4 h-4" />
                <span>
                  {currentView === "trash"
                    ? isMulti
                      ? `Delete permanently (${totalSelected} items)`
                      : "Delete permanently"
                    : isMulti
                    ? `Move to Bin (${totalSelected} items)`
                    : "Move to Bin"}
                </span>
              </button>
              )}
            </div>
          </>
        );
      })()}
    </div>
  );
}

