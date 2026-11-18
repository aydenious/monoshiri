"use client";

import { useQuery } from "@rocicorp/zero/react";
import { useAtom, useAtomValue, useSetAtom } from "jotai";
import { ChevronLeft, ChevronRight, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import type React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { makeFolderMention, mentionedDocumentsAtom } from "@/atoms/chat/mentioned-documents.atom";
import { deleteDocumentMutationAtom } from "@/atoms/documents/document-mutation.atoms";
import { openDocumentViewerAtom } from "@/atoms/documents/document-viewer.atom";
import { expandedFolderIdsAtom } from "@/atoms/documents/folder.atoms";
import { agentCreatedDocumentsAtom } from "@/atoms/documents/ui.atoms";
import { openEditorPanelAtom } from "@/atoms/editor/editor-panel.atom";
import { CreateFolderDialog } from "@/components/documents/CreateFolderDialog";
import { DocumentsFilters } from "@/components/documents/DocumentsFilters";
import { DocumentsView } from "@/components/documents/DocumentsView";
import { FolderPickerDialog } from "@/components/documents/FolderPickerDialog";
import { VersionHistoryDialog } from "@/components/documents/version-history";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { DocumentTypeEnum } from "@/contracts/types/document.types";
import { useArtifactsByDocument } from "@/features/artifacts/hooks/use-artifacts-by-document";
import { openArtifactPanelAtom } from "@/features/artifacts/state/artifact-panel.atom";
import { downloadFile } from "@/features/file-viewers/download-file-button";
import { useDocumentsViewModel } from "@/hooks/use-documents-view-model";
import { useGitRemoteStatus } from "@/hooks/use-git-remote-status";
import { useMediaQuery } from "@/hooks/use-media-query";
import { foldersApiService } from "@/lib/apis/folders-api.service";
import { authenticatedFetch } from "@/lib/auth-fetch";
import { getMentionDocKey } from "@/lib/chat/mention-doc-key";
import { documentDownloadTarget } from "@/lib/documents/document-download";
import type { DocumentNodeDoc, FolderDisplay } from "@/lib/documents/document-tree-types";
import {
	folderDeletionTouchesSync,
	gitMountFolderId,
	gitRepoLabel,
	isSyncedContent,
} from "@/lib/documents/git-synced-folders";
import { buildBackendUrl } from "@/lib/env-config";
import { getWorkspaceIdNumber } from "@/lib/route-params";
import { queries } from "@/zero/queries/index";
import { SidebarSlideOutPanel } from "../sidebar/SidebarSlideOutPanel";

const NON_DELETABLE_DOCUMENT_TYPES: readonly string[] = ["USER_MEMORY", "TEAM_MEMORY"];
const MEMORY_DOCUMENTS: DocumentNodeDoc[] = [
	{
		id: -1001,
		title: "MEMORY.md",
		document_type: "USER_MEMORY",
		folderId: null,
		createdAt: 0,
		status: { state: "ready" },
	},
	{
		id: -1002,
		title: "TEAM_MEMORY.md",
		document_type: "TEAM_MEMORY",
		folderId: null,
		createdAt: 0,
		status: { state: "ready" },
	},
];

function isMemoryDocument(doc: { document_type: string }) {
	return doc.document_type === "USER_MEMORY" || doc.document_type === "TEAM_MEMORY";
}

interface DocumentRightPanelProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	isDocked?: boolean;
	onDockedChange?: (docked: boolean) => void;
	/** When true, renders content without any wrapper — parent provides the container */
	embedded?: boolean;
	/** Uses the page-style heading and spacing reserved for mobile workspace destinations. */
	workspaceView?: boolean;
	/** Optional action element rendered in the header row (e.g. collapse button) */
	headerAction?: React.ReactNode;
}

export function DocumentRightPanel({
	open,
	onOpenChange,
	isDocked = false,
	onDockedChange,
	embedded = false,
	workspaceView = false,
	headerAction,
}: DocumentRightPanelProps) {
	const t = useTranslations("documents");
	const tSidebar = useTranslations("sidebar");
	const params = useParams();
	const isMobile = !useMediaQuery("(min-width: 768px)");
	const workspaceId = getWorkspaceIdNumber(params) ?? 0;
	const { remote: gitRemote, needsAttention: syncNeedsAttention } = useGitRemoteStatus(workspaceId);
	const gitRemoteSettingsHref = `/dashboard/${workspaceId}/workspace-settings/git-remote`;
	const openArtifactPanel = useSetAtom(openArtifactPanelAtom);
	const openDocumentViewer = useSetAtom(openDocumentViewerAtom);
	const openEditorPanel = useSetAtom(openEditorPanelAtom);
	const artifactsByDocument = useArtifactsByDocument(workspaceId);

	const [search, setSearch] = useState("");
	const [activeTypes, setActiveTypes] = useState<DocumentTypeEnum[]>([]);
	const [folderPendingDelete, setFolderPendingDelete] = useState<FolderDisplay | null>(null);
	const [isDeletingFolder, setIsDeletingFolder] = useState(false);
	const [docPendingDelete, setDocPendingDelete] = useState<DocumentNodeDoc | null>(null);
	const [isDeletingDoc, setIsDeletingDoc] = useState(false);
	const { mutateAsync: deleteDocumentMutation } = useAtomValue(deleteDocumentMutationAtom);

	const [sidebarDocs, setSidebarDocs] = useAtom(mentionedDocumentsAtom);
	const mentionedDocKeys = useMemo(
		() => new Set(sidebarDocs.map((d) => getMentionDocKey(d))),
		[sidebarDocs]
	);

	// Folder state
	const [expandedFolderMap, setExpandedFolderMap] = useAtom(expandedFolderIdsAtom);
	const expandedIds = useMemo(
		() => new Set(expandedFolderMap[workspaceId] ?? []),
		[expandedFolderMap, workspaceId]
	);
	const toggleFolderExpand = useCallback(
		(folderId: number) => {
			setExpandedFolderMap((prev) => {
				const current = new Set(prev[workspaceId] ?? []);
				if (current.has(folderId)) current.delete(folderId);
				else current.add(folderId);
				return { ...prev, [workspaceId]: [...current] };
			});
		},
		[workspaceId, setExpandedFolderMap]
	);

	// Zero queries for tree data
	const [zeroFolders, zeroFoldersResult] = useQuery(queries.folders.bySpace({ workspaceId }));
	const [zeroAllDocs, zeroAllDocsResult] = useQuery(queries.documents.bySpace({ workspaceId }));
	const [agentCreatedDocs, setAgentCreatedDocs] = useAtom(agentCreatedDocumentsAtom);

	const treeFolders: FolderDisplay[] = useMemo(
		() =>
			(zeroFolders ?? []).map((f) => ({
				id: f.id,
				name: f.name,
				position: f.position,
				parentId: f.parentId ?? null,
				workspaceId: f.workspaceId,
				metadata: f.metadata as Record<string, unknown> | null | undefined,
			})),
		[zeroFolders]
	);

	const treeDocuments: DocumentNodeDoc[] = useMemo(() => {
		const zeroDocs = (zeroAllDocs ?? [])
			.filter((d) => {
				if (!d.title || d.title.trim() === "") return false;
				const state = (d.status as { state?: string } | undefined)?.state;
				if (state === "deleting") return false;
				return true;
			})
			.map((d) => ({
				id: d.id,
				title: d.title,
				document_type: d.documentType,
				folderId: (d as { folderId?: number | null }).folderId ?? null,
				createdAt: d.createdAt,
				status: d.status as { state: string; reason?: string | null } | undefined,
				artifactFormat: artifactsByDocument.get(d.id)?.format,
			}));

		const zeroIds = new Set(zeroDocs.map((d) => d.id));

		const pendingAgentDocs = agentCreatedDocs
			.filter((d) => d.workspaceId === workspaceId && !zeroIds.has(d.id))
			.map((d) => ({
				id: d.id,
				title: d.title,
				document_type: d.documentType,
				folderId: d.folderId ?? null,
				createdAt: d.createdAt,
				status: { state: "ready" } as { state: string; reason?: string | null },
			}));

		return [...pendingAgentDocs, ...zeroDocs];
	}, [zeroAllDocs, agentCreatedDocs, workspaceId, artifactsByDocument]);

	// Prune agent-created docs once Zero has caught up
	useEffect(() => {
		if (!zeroAllDocs?.length || !agentCreatedDocs.length) return;
		const zeroIds = new Set(zeroAllDocs.map((d) => d.id));
		const remaining = agentCreatedDocs.filter((d) => !zeroIds.has(d.id));
		if (remaining.length < agentCreatedDocs.length) {
			setAgentCreatedDocs(remaining);
		}
	}, [zeroAllDocs, agentCreatedDocs, setAgentCreatedDocs]);

	const foldersByParent = useMemo(() => {
		const map: Record<string, FolderDisplay[]> = {};
		for (const f of treeFolders) {
			const key = String(f.parentId ?? "root");
			if (!map[key]) map[key] = [];
			map[key].push(f);
		}
		return map;
	}, [treeFolders]);

	const gitMountId = gitMountFolderId(gitRemote);
	const gitSyncedFolderIds = useMemo(
		() => new Set(gitMountId != null ? [gitMountId] : []),
		[gitMountId]
	);
	const gitRepoName = gitRemote ? gitRepoLabel(gitRemote) : "";
	const gitMountFolderName = treeFolders.find((f) => f.id === gitMountId)?.name ?? "";
	const folderPendingDeleteIsSynced =
		folderPendingDelete != null && isSyncedContent(treeFolders, folderPendingDelete.id, gitMountId);
	const folderPendingDeleteContainsSynced =
		folderPendingDelete != null &&
		!folderPendingDeleteIsSynced &&
		folderDeletionTouchesSync(treeFolders, folderPendingDelete.id, gitMountId);
	const folderPendingDeleteInGit = folderPendingDeleteIsSynced || folderPendingDeleteContainsSynced;

	// Folder actions
	const [folderPickerOpen, setFolderPickerOpen] = useState(false);
	const [folderPickerTarget, setFolderPickerTarget] = useState<{
		type: "folder" | "document";
		id: number;
		disabledIds?: Set<number>;
	} | null>(null);

	// Create-folder dialog state
	const [createFolderOpen, setCreateFolderOpen] = useState(false);
	const [createFolderParentId, setCreateFolderParentId] = useState<number | null>(null);

	const createFolderParentName = useMemo(() => {
		if (createFolderParentId === null) return null;
		return treeFolders.find((f) => f.id === createFolderParentId)?.name ?? null;
	}, [createFolderParentId, treeFolders]);

	const handleCreateFolder = useCallback((parentId: number | null) => {
		setCreateFolderParentId(parentId);
		setCreateFolderOpen(true);
	}, []);

	const handleCreateFolderConfirm = useCallback(
		async (name: string) => {
			try {
				await foldersApiService.createFolder({
					name,
					parent_id: createFolderParentId,
					workspace_id: workspaceId,
				});
				toast.success("Folder created");
				if (createFolderParentId !== null) {
					setExpandedFolderMap((prev) => {
						const current = new Set(prev[workspaceId] ?? []);
						current.add(createFolderParentId);
						return { ...prev, [workspaceId]: [...current] };
					});
				}
			} catch (e: unknown) {
				toast.error((e as Error)?.message || "Failed to create folder");
			}
		},
		[createFolderParentId, workspaceId, setExpandedFolderMap]
	);

	const handleRenameFolder = useCallback(async (folder: FolderDisplay, newName: string) => {
		try {
			await foldersApiService.updateFolder(folder.id, { name: newName });
			toast.success("Folder renamed");
		} catch (e: unknown) {
			toast.error((e as Error)?.message || "Failed to rename folder");
		}
	}, []);

	const handleDeleteFolder = useCallback((folder: FolderDisplay) => {
		setFolderPendingDelete(folder);
	}, []);

	const handleConfirmDeleteFolder = useCallback(async () => {
		if (!folderPendingDelete) return;
		setIsDeletingFolder(true);
		try {
			await foldersApiService.deleteFolder(folderPendingDelete.id);
			setFolderPendingDelete(null);
			toast.success("Folder deleted");
		} catch (e: unknown) {
			toast.error((e as Error)?.message || "Failed to delete folder");
		} finally {
			setIsDeletingFolder(false);
		}
	}, [folderPendingDelete]);

	const handleMoveFolder = useCallback(
		(folder: FolderDisplay) => {
			const subtreeIds = new Set<number>();
			function collectSubtree(id: number) {
				subtreeIds.add(id);
				for (const child of foldersByParent[String(id)] ?? []) {
					collectSubtree(child.id);
				}
			}
			collectSubtree(folder.id);
			setFolderPickerTarget({
				type: "folder",
				id: folder.id,
				disabledIds: subtreeIds,
			});
			setFolderPickerOpen(true);
		},
		[foldersByParent]
	);

	const handleMoveDocument = useCallback((doc: DocumentNodeDoc) => {
		setFolderPickerTarget({ type: "document", id: doc.id });
		setFolderPickerOpen(true);
	}, []);

	const handleDownloadDocument = useCallback(
		async (doc: DocumentNodeDoc) => {
			const target = documentDownloadTarget(doc, workspaceId, artifactsByDocument.get(doc.id));
			if (!target) {
				toast.error("Artifact is not available yet");
				return;
			}
			try {
				await downloadFile(target.path, target.filename);
			} catch {
				toast.error("Could not download this file");
			}
		},
		[artifactsByDocument, workspaceId]
	);

	const isExportingKBRef = useRef(false);
	const [exportWarningOpen, setExportWarningOpen] = useState(false);
	const [exportWarningContext, setExportWarningContext] = useState<{
		folder: FolderDisplay;
		pendingCount: number;
	} | null>(null);

	const doExport = useCallback(async (url: string, downloadName: string) => {
		const response = await authenticatedFetch(url, { method: "GET" });
		if (!response.ok) {
			const errorData = await response.json().catch(() => ({ detail: "Export failed" }));
			throw new Error(errorData.detail || "Export failed");
		}

		const blob = await response.blob();
		const blobUrl = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = blobUrl;
		a.download = downloadName;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		URL.revokeObjectURL(blobUrl);
	}, []);

	const handleExportWarningConfirm = useCallback(async () => {
		setExportWarningOpen(false);
		const ctx = exportWarningContext;
		if (!ctx?.folder) return;

		isExportingKBRef.current = true;
		try {
			const safeName =
				ctx.folder.name
					.replace(/[^a-zA-Z0-9 _-]/g, "_")
					.trim()
					.slice(0, 80) || "folder";
			await doExport(
				buildBackendUrl(`/api/v1/workspaces/${workspaceId}/export`, {
					folder_id: ctx.folder.id,
				}),
				`${safeName}.zip`
			);
			toast.success(`Folder "${ctx.folder.name}" exported`);
		} catch (err) {
			console.error("Folder export failed:", err);
			toast.error(err instanceof Error ? err.message : "Export failed");
		} finally {
			isExportingKBRef.current = false;
		}
		setExportWarningContext(null);
	}, [exportWarningContext, workspaceId, doExport]);

	const getPendingCountInSubtree = useCallback(
		(folderId: number): number => {
			const subtreeIds = new Set<number>();
			function collect(id: number) {
				subtreeIds.add(id);
				for (const child of foldersByParent[String(id)] ?? []) {
					collect(child.id);
				}
			}
			collect(folderId);
			return treeDocuments.filter(
				(d) =>
					subtreeIds.has(d.folderId ?? -1) &&
					(d.status?.state === "pending" || d.status?.state === "processing")
			).length;
		},
		[foldersByParent, treeDocuments]
	);

	const handleExportFolder = useCallback(
		async (folder: FolderDisplay) => {
			const folderPendingCount = getPendingCountInSubtree(folder.id);
			if (folderPendingCount > 0) {
				setExportWarningContext({
					folder,
					pendingCount: folderPendingCount,
				});
				setExportWarningOpen(true);
				return;
			}

			isExportingKBRef.current = true;
			try {
				const safeName =
					folder.name
						.replace(/[^a-zA-Z0-9 _-]/g, "_")
						.trim()
						.slice(0, 80) || "folder";
				await doExport(
					buildBackendUrl(`/api/v1/workspaces/${workspaceId}/export`, {
						folder_id: folder.id,
					}),
					`${safeName}.zip`
				);
				toast.success(`Folder "${folder.name}" exported`);
			} catch (err) {
				console.error("Folder export failed:", err);
				toast.error(err instanceof Error ? err.message : "Export failed");
			} finally {
				isExportingKBRef.current = false;
			}
		},
		[workspaceId, getPendingCountInSubtree, doExport]
	);

	const handleFolderPickerSelect = useCallback(
		async (targetFolderId: number | null) => {
			if (!folderPickerTarget) return;
			try {
				if (folderPickerTarget.type === "folder") {
					await foldersApiService.moveFolder(folderPickerTarget.id, {
						new_parent_id: targetFolderId,
					});
					toast.success("Folder moved");
				} else {
					await foldersApiService.moveDocument(folderPickerTarget.id, {
						folder_id: targetFolderId,
					});
					toast.success("Document moved");
				}
			} catch (e: unknown) {
				toast.error((e as Error)?.message || "Failed to move item");
			}
			setFolderPickerTarget(null);
		},
		[folderPickerTarget]
	);

	const handleDropIntoFolder = useCallback(
		async (itemType: "folder" | "document", itemId: number, targetFolderId: number | null) => {
			try {
				if (itemType === "folder") {
					await foldersApiService.moveFolder(itemId, {
						new_parent_id: targetFolderId,
					});
					toast.success("Folder moved");
				} else {
					await foldersApiService.moveDocument(itemId, {
						folder_id: targetFolderId,
					});
					toast.success("Document moved");
				}
			} catch (e: unknown) {
				toast.error((e as Error)?.message || "Failed to move item");
			}
		},
		[]
	);

	const handleReorderFolder = useCallback(
		async (folderId: number, beforePos: string | null, afterPos: string | null) => {
			try {
				await foldersApiService.reorderFolder(folderId, {
					before_position: beforePos,
					after_position: afterPos,
				});
			} catch (e: unknown) {
				toast.error((e as Error)?.message || "Failed to reorder folder");
			}
		},
		[]
	);

	const handleToggleChatMention = useCallback(
		(doc: { id: number; title: string; document_type: string }, isMentioned: boolean) => {
			if (isMemoryDocument(doc)) return;
			const key = getMentionDocKey({ ...doc, kind: "doc" });
			if (isMentioned) {
				setSidebarDocs((prev) => prev.filter((d) => getMentionDocKey(d) !== key));
			} else {
				setSidebarDocs((prev) => {
					if (prev.some((d) => getMentionDocKey(d) === key)) return prev;
					return [
						...prev,
						{
							id: doc.id,
							title: doc.title,
							document_type: doc.document_type as DocumentTypeEnum,
							kind: "doc",
						},
					];
				});
			}
		},
		[setSidebarDocs]
	);

	const handleToggleFolderSelect = useCallback(
		(folderId: number, selectAll: boolean) => {
			// One folder click = one folder-mention chip. The agent
			// resolves the chip to its virtual path
			// (``/documents/MyFolder/``) and walks it itself with
			// ``ls`` / ``find_documents``. We deliberately don't
			// fan out to per-doc chips anymore — the previous
			// behaviour created N chips for one click and dropped
			// nested folders entirely once selected, which the
			// agent had no way to recover.
			const folder = treeFolders.find((f) => f.id === folderId);
			if (!folder) return;
			const chip = makeFolderMention({ id: folder.id, name: folder.name });
			const chipKey = getMentionDocKey(chip);

			if (selectAll) {
				setSidebarDocs((prev) => {
					const exists = prev.some((d) => getMentionDocKey(d) === chipKey);
					return exists ? prev : [...prev, chip];
				});
			} else {
				setSidebarDocs((prev) => prev.filter((d) => getMentionDocKey(d) !== chipKey));
			}
		},
		[treeFolders, setSidebarDocs]
	);

	const openMemoryDocument = useCallback(
		(doc: DocumentNodeDoc) => {
			if (doc.document_type === "USER_MEMORY") {
				openEditorPanel({
					memoryScope: "user",
					workspaceId,
					title: doc.title,
				});
				return true;
			}
			if (doc.document_type === "TEAM_MEMORY") {
				openEditorPanel({
					memoryScope: "team",
					workspaceId,
					title: doc.title,
				});
				return true;
			}
			return false;
		},
		[openEditorPanel, workspaceId]
	);

	const handleResetMemoryDocument = useCallback(
		async (doc: DocumentNodeDoc) => {
			if (!isMemoryDocument(doc)) return;
			if (!window.confirm(`Reset ${doc.title.toLowerCase()}? This clears the memory document.`)) {
				return;
			}
			const endpoint =
				doc.document_type === "USER_MEMORY"
					? buildBackendUrl("/api/v1/users/me/memory/reset")
					: buildBackendUrl(`/api/v1/workspaces/${workspaceId}/memory/reset`);
			try {
				const response = await authenticatedFetch(endpoint, { method: "POST" });
				if (!response.ok) {
					const errorData = await response.json().catch(() => ({ detail: "Reset failed" }));
					throw new Error(errorData.detail || "Reset failed");
				}
				toast.success(`${doc.title} reset`);
				openMemoryDocument(doc);
			} catch (error) {
				toast.error((error as Error)?.message || `Failed to reset ${doc.title.toLowerCase()}`);
			}
		},
		[openMemoryDocument, workspaceId]
	);

	const typeCounts = useMemo(() => {
		const counts: Partial<Record<string, number>> = {};
		for (const d of treeDocuments) {
			const displayType = d.document_type === "LOCAL_FOLDER_FILE" ? "FILE" : d.document_type;
			counts[displayType] = (counts[displayType] || 0) + 1;
		}
		return counts;
	}, [treeDocuments]);

	const deletableSelectedIds = useMemo(() => {
		const treeDocMap = new Map(treeDocuments.map((d) => [d.id, d]));
		return sidebarDocs
			.filter((doc) => {
				if (doc.kind !== "doc") return false;
				const fullDoc = treeDocMap.get(doc.id);
				if (!fullDoc) return false;
				const state = fullDoc.status?.state ?? "ready";
				return (
					state !== "pending" &&
					state !== "processing" &&
					!NON_DELETABLE_DOCUMENT_TYPES.includes(doc.document_type)
				);
			})
			.map((doc) => doc.id);
	}, [sidebarDocs, treeDocuments]);

	const bulkDeleteHitsSync = useMemo(() => {
		if (gitMountId == null) return false;
		const byId = new Map(treeDocuments.map((d) => [d.id, d]));
		return deletableSelectedIds.some((id) =>
			isSyncedContent(treeFolders, byId.get(id)?.folderId, gitMountId)
		);
	}, [deletableSelectedIds, treeDocuments, treeFolders, gitMountId]);

	const [bulkDeleteConfirmOpen, setBulkDeleteConfirmOpen] = useState(false);
	const [isBulkDeleting, setIsBulkDeleting] = useState(false);
	const [versionDocId, setVersionDocId] = useState<number | null>(null);

	const handleBulkDeleteSelected = useCallback(async () => {
		if (deletableSelectedIds.length === 0) return;
		setIsBulkDeleting(true);
		try {
			const results = await Promise.allSettled(
				deletableSelectedIds.map(async (id) => {
					await deleteDocumentMutation({ id });
					return id;
				})
			);
			const successIds = results
				.filter((r): r is PromiseFulfilledResult<number> => r.status === "fulfilled")
				.map((r) => r.value);
			const failed = results.length - successIds.length;
			if (successIds.length > 0) {
				setSidebarDocs((prev) => {
					const idSet = new Set(successIds);
					return prev.filter((d) => !idSet.has(d.id));
				});
				toast.success(`Deleted ${successIds.length} document${successIds.length !== 1 ? "s" : ""}`);
			}
			if (failed > 0) {
				toast.error(`Failed to delete ${failed} document${failed !== 1 ? "s" : ""}`);
			}
		} catch {
			toast.error("Failed to delete documents");
		}
		setIsBulkDeleting(false);
		setBulkDeleteConfirmOpen(false);
	}, [deletableSelectedIds, deleteDocumentMutation, setSidebarDocs]);

	const onToggleType = useCallback((type: DocumentTypeEnum, checked: boolean) => {
		setActiveTypes((prev) => {
			if (checked) {
				return prev.includes(type) ? prev : [...prev, type];
			}
			return prev.filter((t) => t !== type);
		});
	}, []);

	const handleDeleteDocument = useCallback(
		async (id: number): Promise<boolean> => {
			try {
				await deleteDocumentMutation({ id });
				toast.success(t("delete_success") || "Document deleted");
				setSidebarDocs((prev) => prev.filter((d) => d.kind !== "doc" || d.id !== id));
				return true;
			} catch (e) {
				console.error("Error deleting document:", e);
				return false;
			}
		},
		[deleteDocumentMutation, t, setSidebarDocs]
	);

	const requestDeleteDocument = useCallback(
		(doc: DocumentNodeDoc) => {
			if (isSyncedContent(treeFolders, doc.folderId, gitMountId)) {
				setDocPendingDelete(doc);
				return;
			}
			void handleDeleteDocument(doc.id);
		},
		[treeFolders, gitMountId, handleDeleteDocument]
	);

	const handleConfirmDeleteDocument = useCallback(async () => {
		if (!docPendingDelete) return;
		setIsDeletingDoc(true);
		const ok = await handleDeleteDocument(docPendingDelete.id);
		setIsDeletingDoc(false);
		if (ok) setDocPendingDelete(null);
	}, [docPendingDelete, handleDeleteDocument]);

	useEffect(() => {
		const handleEscape = (e: KeyboardEvent) => {
			if (e.key === "Escape" && open && isMobile) {
				onOpenChange(false);
			}
		};
		document.addEventListener("keydown", handleEscape);
		return () => document.removeEventListener("keydown", handleEscape);
	}, [open, onOpenChange, isMobile]);

	const documentsViewModel = useDocumentsViewModel({
		folders: treeFolders,
		documents: treeDocuments,
		pinnedDocuments: MEMORY_DOCUMENTS,
		query: search,
		activeTypes,
		isLoading: zeroFoldersResult.type !== "complete" || zeroAllDocsResult.type !== "complete",
	});
	const handleOpenSearchFolder = useCallback(
		(folder: FolderDisplay) => {
			const folderById = new Map(treeFolders.map((item) => [item.id, item]));
			const folderIds = new Set<number>([folder.id]);
			let parentId = folder.parentId;
			while (parentId !== null && !folderIds.has(parentId)) {
				folderIds.add(parentId);
				parentId = folderById.get(parentId)?.parentId ?? null;
			}

			setExpandedFolderMap((previous) => {
				const expanded = new Set(previous[workspaceId] ?? []);
				for (const folderId of folderIds) expanded.add(folderId);
				return { ...previous, [workspaceId]: [...expanded] };
			});
			setSearch("");
		},
		[setExpandedFolderMap, treeFolders, workspaceId]
	);

	const cloudContent = (
		<>
			<div className="flex min-h-0 min-w-0 flex-1 flex-col pt-0">
				<div className={`${workspaceView ? "" : "px-4"} pb-1.5 pt-8`}>
					<DocumentsFilters
						typeCounts={typeCounts}
						onSearch={setSearch}
						searchValue={search}
						onToggleType={onToggleType}
						activeTypes={activeTypes}
						onCreateFolder={() => handleCreateFolder(null)}
						connectRepoHref={gitRemoteSettingsHref}
						repoConnected={Boolean(gitRemote)}
						syncNeedsAttention={syncNeedsAttention}
					/>
				</div>

				<div className="relative min-h-0 min-w-0 flex-1 overflow-auto">
					{deletableSelectedIds.length > 0 && (
						<div className="absolute inset-x-0 top-0 z-10 flex items-center justify-center px-4 py-1.5 animate-in fade-in duration-150 pointer-events-none">
							<Button
								type="button"
								variant="destructive"
								size="sm"
								onClick={() => setBulkDeleteConfirmOpen(true)}
								className="pointer-events-auto h-auto gap-1.5 px-3 py-1 text-xs shadow-lg"
							>
								<Trash2 size={12} />
								Delete {deletableSelectedIds.length}{" "}
								{deletableSelectedIds.length === 1 ? "item" : "items"}
							</Button>
						</div>
					)}

					<DocumentsView
						viewModel={documentsViewModel}
						onOpenFolder={handleOpenSearchFolder}
						expandedIds={expandedIds}
						onToggleExpand={toggleFolderExpand}
						mentionedDocKeys={mentionedDocKeys}
						onToggleChatMention={handleToggleChatMention}
						onToggleFolderSelect={handleToggleFolderSelect}
						onRenameFolder={handleRenameFolder}
						onDeleteFolder={handleDeleteFolder}
						onMoveFolder={handleMoveFolder}
						onCreateFolder={handleCreateFolder}
						onPreviewDocument={(doc) => {
							if (openMemoryDocument(doc)) return;
							if (doc.document_type === "ARTIFACT") {
								const artifact = artifactsByDocument.get(doc.id);
								if (artifact) {
									openArtifactPanel({ artifactId: artifact.artifact_id });
								} else {
									toast.error("Artifact is not available yet");
								}
								return;
							}
							openDocumentViewer({
								documentId: doc.id,
								workspaceId,
								title: doc.title,
							});
						}}
						onDeleteDocument={requestDeleteDocument}
						onMoveDocument={handleMoveDocument}
						onDownloadDocument={handleDownloadDocument}
						onResetDocument={handleResetMemoryDocument}
						onVersionHistory={(doc) => setVersionDocId(doc.id)}
						onDropIntoFolder={handleDropIntoFolder}
						onReorderFolder={handleReorderFolder}
						onExportFolder={handleExportFolder}
						gitSyncedFolderIds={gitSyncedFolderIds}
						gitSyncedTooltip={gitRepoName ? `Synced with ${gitRepoName}` : undefined}
					/>
				</div>
			</div>
		</>
	);

	const documentsContent = (
		<>
			{workspaceView ? (
				<header className="shrink-0">
					<h1 className="text-xl font-semibold text-foreground md:text-2xl">
						{t("title") || "Documents"}
					</h1>
				</header>
			) : (
				<div className="flex h-12 min-w-0 shrink-0 items-center px-3">
					<div className="flex min-w-0 w-full items-center justify-between">
						<div className="flex min-w-0 items-center gap-3">
							{isMobile && (
								<Button
									variant="ghost"
									size="icon"
									className="h-8 w-8 rounded-full text-muted-foreground hover:text-accent-foreground"
									onClick={() => onOpenChange(false)}
								>
									<ChevronLeft className="h-4 w-4" />
									<span className="sr-only">{tSidebar("close") || "Close"}</span>
								</Button>
							)}
							<h2 className="min-w-0 truncate text-lg font-semibold select-none">
								{t("title") || "Documents"}
							</h2>
						</div>
						<div className="flex items-center gap-1">
							{!isMobile && onDockedChange && (
								<Tooltip>
									<TooltipTrigger asChild>
										<Button
											variant="ghost"
											size="icon"
											className="h-8 w-8 rounded-full text-muted-foreground hover:text-accent-foreground"
											onClick={() => {
												if (isDocked) {
													onDockedChange(false);
													onOpenChange(false);
												} else {
													onDockedChange(true);
												}
											}}
										>
											{isDocked ? (
												<ChevronLeft className="h-4 w-4" />
											) : (
												<ChevronRight className="h-4 w-4" />
											)}
											<span className="sr-only">
												{isDocked ? "Collapse panel" : "Expand panel"}
											</span>
										</Button>
									</TooltipTrigger>
									<TooltipContent className="z-80">
										{isDocked ? "Collapse panel" : "Expand panel"}
									</TooltipContent>
								</Tooltip>
							)}
							{headerAction}
						</div>
					</div>
				</div>
			)}
			{cloudContent}

			{versionDocId !== null && (
				<VersionHistoryDialog
					open
					onOpenChange={(open) => {
						if (!open) setVersionDocId(null);
					}}
					documentId={versionDocId}
				/>
			)}

			<FolderPickerDialog
				open={folderPickerOpen}
				onOpenChange={setFolderPickerOpen}
				folders={treeFolders}
				title={folderPickerTarget?.type === "folder" ? "Move folder to" : "Move document to"}
				description="Select a destination folder, or choose Root to move to the top level."
				disabledFolderIds={folderPickerTarget?.disabledIds}
				onSelect={handleFolderPickerSelect}
			/>

			<CreateFolderDialog
				open={createFolderOpen}
				onOpenChange={setCreateFolderOpen}
				parentFolderName={createFolderParentName}
				onConfirm={handleCreateFolderConfirm}
			/>

			<AlertDialog
				open={folderPendingDelete !== null}
				onOpenChange={(open) => {
					if (!open && !isDeletingFolder) setFolderPendingDelete(null);
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							{folderPendingDeleteIsSynced
								? "Delete a synced folder?"
								: folderPendingDeleteContainsSynced
									? "Delete a folder with synced files?"
									: "Delete this folder?"}
						</AlertDialogTitle>
						<AlertDialogDescription>
							{folderPendingDeleteIsSynced ? (
								<>
									<span className="font-medium text-foreground">{folderPendingDelete?.name}</span>{" "}
									is synced with <span className="font-medium text-foreground">{gitRepoName}</span>.
									Deleting it also deletes its documents from{" "}
									<span className="font-medium text-foreground">{gitRepoName}</span> on the next
									sync. To remove only the local copy and keep the remote,{" "}
									<Link href={gitRemoteSettingsHref} className="font-medium underline">
										disconnect the repository
									</Link>{" "}
									first.
								</>
							) : folderPendingDeleteContainsSynced ? (
								<>
									<span className="font-medium text-foreground">{folderPendingDelete?.name}</span>{" "}
									contains{" "}
									<span className="font-medium text-foreground">
										{gitMountFolderName || "a synced folder"}
									</span>
									, which is synced with{" "}
									<span className="font-medium text-foreground">{gitRepoName}</span>. Deleting it
									also deletes those synced documents from{" "}
									<span className="font-medium text-foreground">{gitRepoName}</span> on the next
									sync. To remove only the local copy and keep the remote,{" "}
									<Link href={gitRemoteSettingsHref} className="font-medium underline">
										disconnect the repository
									</Link>{" "}
									first.
								</>
							) : (
								<>
									<span className="font-medium text-foreground">{folderPendingDelete?.name}</span>{" "}
									and all of its contents will be permanently deleted. This action cannot be undone.
								</>
							)}
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={isDeletingFolder}>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={(event) => {
								event.preventDefault();
								void handleConfirmDeleteFolder();
							}}
							disabled={isDeletingFolder}
							className="relative bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							<span className={isDeletingFolder ? "opacity-0" : ""}>
								{folderPendingDeleteInGit ? "Delete anyway" : "Delete"}
							</span>
							{isDeletingFolder ? <Spinner size="sm" className="absolute" /> : null}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			<AlertDialog
				open={docPendingDelete !== null}
				onOpenChange={(open) => {
					if (!open && !isDeletingDoc) setDocPendingDelete(null);
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete a synced document?</AlertDialogTitle>
						<AlertDialogDescription>
							<span className="font-medium text-foreground">{docPendingDelete?.title}</span> is
							synced with <span className="font-medium text-foreground">{gitRepoName}</span>.
							Deleting it here also removes it from the remote on the next sync. To keep the remote
							and remove only the local copy,{" "}
							<Link href={gitRemoteSettingsHref} className="font-medium underline">
								disconnect the repository
							</Link>{" "}
							first.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={isDeletingDoc}>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={(event) => {
								event.preventDefault();
								void handleConfirmDeleteDocument();
							}}
							disabled={isDeletingDoc}
							className="relative bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							<span className={isDeletingDoc ? "opacity-0" : ""}>Delete anyway</span>
							{isDeletingDoc ? <Spinner size="sm" className="absolute" /> : null}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			<AlertDialog
				open={bulkDeleteConfirmOpen}
				onOpenChange={(open) => !open && !isBulkDeleting && setBulkDeleteConfirmOpen(false)}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Delete {deletableSelectedIds.length} document
							{deletableSelectedIds.length !== 1 ? "s" : ""}?
						</AlertDialogTitle>
						<AlertDialogDescription>
							This action cannot be undone.{" "}
							{deletableSelectedIds.length === 1
								? "This document"
								: `These ${deletableSelectedIds.length} documents`}{" "}
							will be permanently deleted from your workspace.
							{bulkDeleteHitsSync ? (
								<>
									{" "}
									Some are synced with{" "}
									<span className="font-medium text-foreground">{gitRepoName}</span> and will also
									be removed from the remote on the next sync.
								</>
							) : null}
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={isBulkDeleting}>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={(e) => {
								e.preventDefault();
								handleBulkDeleteSelected();
							}}
							disabled={isBulkDeleting}
							className="relative bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							<span className={isBulkDeleting ? "opacity-0" : ""}>Delete</span>
							{isBulkDeleting && <Spinner size="sm" className="absolute" />}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			<AlertDialog
				open={exportWarningOpen}
				onOpenChange={(open) => {
					if (!open) {
						setExportWarningOpen(false);
						setExportWarningContext(null);
					}
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Some documents are still processing</AlertDialogTitle>
						<AlertDialogDescription>
							{exportWarningContext?.pendingCount} document
							{exportWarningContext?.pendingCount !== 1 ? "s are" : " is"} currently being processed
							and will be excluded from the export. Do you want to continue?
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction onClick={handleExportWarningConfirm}>
							Export anyway
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);

	if (embedded) {
		return (
			<div
				className={
					workspaceView
						? "@container flex h-full min-h-0 min-w-0 w-full flex-col bg-panel text-foreground"
						: "@container flex h-full min-h-0 min-w-0 w-full flex-col bg-sidebar text-sidebar-foreground"
				}
			>
				{documentsContent}
			</div>
		);
	}

	if (isDocked && open && !isMobile) {
		return (
			<aside
				className="h-full w-[380px] shrink-0 bg-sidebar text-sidebar-foreground flex flex-col border-r"
				aria-label={t("title") || "Documents"}
			>
				{documentsContent}
			</aside>
		);
	}

	return (
		<SidebarSlideOutPanel
			open={open}
			onOpenChange={onOpenChange}
			ariaLabel={t("title") || "Documents"}
			width={isMobile ? undefined : 380}
		>
			{documentsContent}
		</SidebarSlideOutPanel>
	);
}
