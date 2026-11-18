"use client";

import { useAtomValue, useSetAtom } from "jotai";
import { Check, Copy, FileQuestionMark, Pencil, RefreshCw, XIcon } from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { closeEditorPanelAtom, editorPanelAtom } from "@/atoms/editor/editor-panel.atom";
import { PlateErrorBoundary } from "@/components/editor/plate-error-boundary";
import { SourceCodeEditor } from "@/components/editor/source-code-editor";
import {
	fetchMemoryEditorDocument,
	getMemoryLimitState,
	type MemoryLimits,
	saveMemoryMarkdown,
} from "@/components/editor-panel/memory";
import { Button } from "@/components/ui/button";
import { Drawer, DrawerContent, DrawerHandle, DrawerTitle } from "@/components/ui/drawer";
import { Separator } from "@/components/ui/separator";
import { Spinner } from "@/components/ui/spinner";
import { useMediaQuery } from "@/hooks/use-media-query";

const PlateEditor = dynamic(
	() => import("@/components/editor/plate-editor").then((m) => ({ default: m.PlateEditor })),
	{ ssr: false, loading: () => <EditorPanelSkeleton /> }
);

interface EditorContent {
	document_id: number;
	title: string;
	document_type?: string;
	source_markdown: string;
}

function EditorPanelSkeleton() {
	return (
		<div className="space-y-6 p-6">
			<div className="h-6 w-3/4 rounded-md bg-muted/60 animate-pulse" />
			<div className="space-y-2.5">
				<div className="h-3 w-full rounded-md bg-muted/60 animate-pulse" />
				<div className="h-3 w-[95%] rounded-md bg-muted/60 animate-pulse [animation-delay:100ms]" />
				<div className="h-3 w-[88%] rounded-md bg-muted/60 animate-pulse [animation-delay:200ms]" />
				<div className="h-3 w-[60%] rounded-md bg-muted/60 animate-pulse [animation-delay:300ms]" />
			</div>
			<div className="h-5 w-2/5 rounded-md bg-muted/60 animate-pulse [animation-delay:400ms]" />
			<div className="space-y-2.5">
				<div className="h-3 w-full rounded-md bg-muted/60 animate-pulse [animation-delay:500ms]" />
				<div className="h-3 w-[92%] rounded-md bg-muted/60 animate-pulse [animation-delay:600ms]" />
				<div className="h-3 w-[75%] rounded-md bg-muted/60 animate-pulse [animation-delay:700ms]" />
			</div>
		</div>
	);
}

export function EditorPanelContent({
	memoryScope,
	workspaceId,
	title,
	onClose,
}: {
	memoryScope?: "user" | "team";
	workspaceId?: number;
	title: string | null;
	onClose?: () => void;
}) {
	const [editorDoc, setEditorDoc] = useState<EditorContent | null>(null);
	const [isLoading, setIsLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	const [isEditing, setIsEditing] = useState(false);
	const [memoryLimits, setMemoryLimits] = useState<MemoryLimits | null>(null);

	const [editedMarkdown, setEditedMarkdown] = useState<string | null>(null);
	const [hasCopied, setHasCopied] = useState(false);
	const markdownRef = useRef<string>("");
	const copyResetTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const initialLoadDone = useRef(false);
	const changeCountRef = useRef(0);
	const [displayTitle, setDisplayTitle] = useState(title || "Untitled");

	useEffect(() => {
		const controller = new AbortController();
		setIsLoading(true);
		setError(null);
		setEditorDoc(null);
		setEditedMarkdown(null);
		setHasCopied(false);
		setIsEditing(false);
		setMemoryLimits(null);
		initialLoadDone.current = false;
		changeCountRef.current = 0;

		const doFetch = async () => {
			try {
				if (!memoryScope) throw new Error("Missing memory context");
				const { document, limits } = await fetchMemoryEditorDocument({
					scope: memoryScope,
					workspaceId,
					title,
					signal: controller.signal,
				});
				if (controller.signal.aborted) return;
				setMemoryLimits(limits);
				const content: EditorContent = document;
				markdownRef.current = content.source_markdown;
				setDisplayTitle(content.title);
				setEditorDoc(content);
				initialLoadDone.current = true;
			} catch (err) {
				if (controller.signal.aborted) return;
				console.error("Error fetching document:", err);
				setError(err instanceof Error ? err.message : "Failed to fetch document");
			} finally {
				if (!controller.signal.aborted) setIsLoading(false);
			}
		};

		doFetch().catch(() => {});
		return () => controller.abort();
	}, [memoryScope, workspaceId, title]);

	useEffect(() => {
		return () => {
			if (copyResetTimeoutRef.current) {
				clearTimeout(copyResetTimeoutRef.current);
			}
		};
	}, []);

	const handleMarkdownChange = useCallback(
		(md: string) => {
			if (!isEditing) return;

			markdownRef.current = md;
			if (!initialLoadDone.current) return;
			changeCountRef.current += 1;
			if (changeCountRef.current <= 1) return;

			const savedContent = editorDoc?.source_markdown ?? "";
			setEditedMarkdown(md === savedContent ? null : md);
		},
		[editorDoc?.source_markdown, isEditing]
	);

	const handleCopy = useCallback(async () => {
		try {
			const textToCopy = markdownRef.current ?? editorDoc?.source_markdown ?? "";
			await navigator.clipboard.writeText(textToCopy);
			setHasCopied(true);
			if (copyResetTimeoutRef.current) {
				clearTimeout(copyResetTimeoutRef.current);
			}
			copyResetTimeoutRef.current = setTimeout(() => {
				setHasCopied(false);
			}, 1400);
		} catch (err) {
			console.error("Error copying content:", err);
		}
	}, [editorDoc?.source_markdown]);

	const handleSave = useCallback(
		async (options?: { silent?: boolean }) => {
			setSaving(true);
			try {
				if (!memoryScope) throw new Error("Missing memory context");
				const { markdown: savedContent, limits } = await saveMemoryMarkdown({
					scope: memoryScope,
					workspaceId,
					markdown: markdownRef.current,
				});
				markdownRef.current = savedContent;
				setMemoryLimits(limits ?? memoryLimits);
				setEditorDoc((prev) => (prev ? { ...prev, source_markdown: savedContent } : prev));
				setEditedMarkdown(null);
				if (!options?.silent) {
					toast.success("Memory saved");
				}
				return true;
			} catch (err) {
				console.error("Error saving document:", err);
				if (!options?.silent) {
					toast.error(err instanceof Error ? err.message : "Failed to save document");
				}
				return false;
			} finally {
				setSaving(false);
			}
		},
		[memoryLimits, memoryScope, workspaceId]
	);

	const isEditableType = editorDoc !== null;
	const hasUnsavedChanges = editedMarkdown !== null;
	const showDesktopHeader = !!onClose;
	const showEditingActions = isEditableType && isEditing;
	const activeMarkdown = editedMarkdown ?? editorDoc?.source_markdown ?? "";
	const memoryLimitState = getMemoryLimitState(activeMarkdown.length, memoryLimits);
	const memoryCounterClassName =
		memoryLimitState?.level === "error"
			? "text-red-500"
			: memoryLimitState?.level === "warning"
				? "text-orange-500"
				: "text-muted-foreground";
	const saveDisabled = saving || !hasUnsavedChanges || (memoryLimitState?.isOverLimit ?? false);
	const editorInstanceKey = `memory-${memoryScope ?? "user"}-${isEditing ? "editing" : "viewing"}`;

	const handleCancelEditing = useCallback(() => {
		const savedContent = editorDoc?.source_markdown ?? "";
		markdownRef.current = savedContent;
		setEditedMarkdown(null);
		changeCountRef.current = 0;
		setIsEditing(false);
	}, [editorDoc?.source_markdown]);

	const headerActions = showEditingActions ? (
		<>
			<Button
				variant="ghost"
				size="sm"
				className="h-6 px-2 text-xs"
				onClick={handleCancelEditing}
				disabled={saving}
			>
				Cancel
			</Button>
			<Button
				variant="secondary"
				size="sm"
				className="relative h-6 w-[56px] px-0 text-xs"
				onClick={async () => {
					const saveSucceeded = await handleSave({ silent: true });
					if (saveSucceeded) setIsEditing(false);
				}}
				disabled={saveDisabled}
			>
				<span className={saving ? "opacity-0" : ""}>Save</span>
				{saving && <Spinner size="xs" className="absolute" />}
			</Button>
		</>
	) : (
		<>
			<Button
				variant="ghost"
				size="icon"
				className="size-6"
				onClick={() => {
					void handleCopy();
				}}
				disabled={isLoading || !editorDoc}
			>
				{hasCopied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
				<span className="sr-only">{hasCopied ? "Copied file contents" : "Copy file contents"}</span>
			</Button>
			{isEditableType && (
				<Button
					variant="ghost"
					size="icon"
					className="size-6"
					onClick={() => {
						changeCountRef.current = 0;
						setEditedMarkdown(null);
						setIsEditing(true);
					}}
				>
					<Pencil className="size-3.5" />
					<span className="sr-only">Edit document</span>
				</Button>
			)}
		</>
	);

	return (
		<>
			{showDesktopHeader ? (
				<div className="shrink-0">
					<div className="grid h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b px-4">
						<div className="min-w-0 flex flex-1 items-center gap-2">
							<p className="truncate text-sm text-muted-foreground">{displayTitle}</p>
							{memoryLimitState && (
								<>
									<Separator
										orientation="vertical"
										className="mx-1.5 bg-muted-foreground/20 data-[orientation=vertical]:h-4 data-[orientation=vertical]:w-px dark:bg-muted-foreground/25"
									/>
									<span className={`shrink-0 text-xs ${memoryCounterClassName}`}>
										{memoryLimitState.label}
									</span>
								</>
							)}
						</div>
						<div className="flex items-center gap-1 shrink-0">
							{headerActions}
							<Separator
								orientation="vertical"
								className="mx-1.5 bg-muted-foreground/20 data-[orientation=vertical]:h-4 data-[orientation=vertical]:w-px dark:bg-muted-foreground/25"
							/>
							<Button
								variant="ghost"
								size="icon"
								onClick={onClose}
								className="size-6 shrink-0 rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
							>
								<XIcon className="size-4" />
								<span className="sr-only">Close editor panel</span>
							</Button>
						</div>
					</div>
				</div>
			) : (
				<div className="flex h-14 items-center justify-between border-b px-4 shrink-0">
					<div className="flex flex-1 min-w-0 items-center gap-2">
						<h2 className="text-sm font-semibold truncate">{displayTitle}</h2>
						{memoryLimitState && (
							<>
								<Separator
									orientation="vertical"
									className="mx-1 bg-border data-[orientation=vertical]:h-4 data-[orientation=vertical]:w-px dark:bg-white/10"
								/>
								<span className={`shrink-0 text-xs ${memoryCounterClassName}`}>
									{memoryLimitState.label}
								</span>
							</>
						)}
					</div>
					<div className="flex items-center gap-1 shrink-0">{headerActions}</div>
				</div>
			)}

			<div className="flex-1 overflow-hidden">
				{isLoading ? (
					<EditorPanelSkeleton />
				) : error || !editorDoc ? (
					<div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
						{error?.toLowerCase().includes("still being processed") ? (
							<div className="rounded-full bg-muted/50 p-3">
								<RefreshCw className="size-6 text-muted-foreground animate-spin" />
							</div>
						) : (
							<div className="rounded-full bg-muted/50 p-3">
								<FileQuestionMark className="size-6 text-muted-foreground" />
							</div>
						)}
						<div className="space-y-1 max-w-xs">
							<p className="font-medium text-foreground">
								{error?.toLowerCase().includes("still being processed")
									? "Document is processing"
									: "Document unavailable"}
							</p>
							<p className="text-sm text-muted-foreground">
								{error || "An unknown error occurred"}
							</p>
						</div>
					</div>
				) : (
					<div className="flex h-full min-h-0 flex-col">
						<div className="flex-1 min-h-0 overflow-hidden">
							<PlateErrorBoundary
								key={`plate-boundary-${editorInstanceKey}`}
								fallback={
									<SourceCodeEditor
										path={`${editorDoc.title || "document"}.md`}
										language="markdown"
										value={editorDoc.source_markdown}
										readOnly
										onChange={() => {}}
									/>
								}
							>
								<PlateEditor
									key={editorInstanceKey}
									preset="full"
									markdown={editorDoc.source_markdown}
									onMarkdownChange={handleMarkdownChange}
									readOnly={!isEditing}
									placeholder="Start writing..."
									editorVariant="default"
									allowModeToggle={false}
									reserveToolbarSpace
									defaultEditing={isEditing}
									className="**:[[role=toolbar]]:bg-sidebar!"
									// Render `[citation:N]` badges in view mode only.
									// Edit mode keeps raw text so the user can edit/delete
									// tokens directly. Memory documents never carry citations.
									enableCitations={false}
								/>
							</PlateErrorBoundary>
						</div>
					</div>
				)}
			</div>
		</>
	);
}

function DesktopEditorPanel() {
	const panelState = useAtomValue(editorPanelAtom);
	const closePanel = useSetAtom(closeEditorPanelAtom);

	useEffect(() => {
		const handleKeyDown = (e: KeyboardEvent) => {
			if (e.key === "Escape") closePanel();
		};
		document.addEventListener("keydown", handleKeyDown);
		return () => document.removeEventListener("keydown", handleKeyDown);
	}, [closePanel]);

	if (!panelState.isOpen || !panelState.memoryScope) return null;

	return (
		<div className="flex w-[50%] max-w-[700px] min-w-[380px] flex-col border-l bg-sidebar text-sidebar-foreground animate-in slide-in-from-right-4 duration-300 ease-out">
			<EditorPanelContent
				memoryScope={panelState.memoryScope ?? undefined}
				workspaceId={panelState.workspaceId ?? undefined}
				title={panelState.title}
				onClose={closePanel}
			/>
		</div>
	);
}

function MobileEditorDrawer() {
	const panelState = useAtomValue(editorPanelAtom);
	const closePanel = useSetAtom(closeEditorPanelAtom);

	if (!panelState.memoryScope) return null;

	return (
		<Drawer
			open={panelState.isOpen}
			onOpenChange={(open) => {
				if (!open) closePanel();
			}}
			shouldScaleBackground={false}
		>
			<DrawerContent
				className="h-[90vh] max-h-[90vh] z-80 bg-sidebar overflow-hidden"
				overlayClassName="z-80"
			>
				<DrawerHandle />
				<DrawerTitle className="sr-only">{panelState.title || "Editor"}</DrawerTitle>
				<div className="min-h-0 flex-1 flex flex-col overflow-hidden">
					<EditorPanelContent
						memoryScope={panelState.memoryScope ?? undefined}
						workspaceId={panelState.workspaceId ?? undefined}
						title={panelState.title}
					/>
				</div>
			</DrawerContent>
		</Drawer>
	);
}

export function EditorPanel() {
	const panelState = useAtomValue(editorPanelAtom);
	const isDesktop = useMediaQuery("(min-width: 1024px)");

	if (!panelState.isOpen || !panelState.memoryScope) return null;

	if (isDesktop) {
		return <DesktopEditorPanel />;
	}

	return <MobileEditorDrawer />;
}

export function MobileEditorPanel() {
	const panelState = useAtomValue(editorPanelAtom);
	const isDesktop = useMediaQuery("(min-width: 1024px)");

	if (isDesktop || !panelState.isOpen || !panelState.memoryScope) return null;

	return <MobileEditorDrawer />;
}
