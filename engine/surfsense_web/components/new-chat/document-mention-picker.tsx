"use client";

import { useQuery as useZeroQuery } from "@rocicorp/zero/react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Files, MessageCircleMore } from "lucide-react";
import {
	Fragment,
	forwardRef,
	type UIEvent,
	useCallback,
	useDeferredValue,
	useEffect,
	useMemo,
	useRef,
	useState,
} from "react";
import {
	type MentionedDocumentInfo,
	makeThreadMention,
} from "@/atoms/chat/mentioned-documents.atom";
import { MentionIcon } from "@/components/assistant-ui/mention-icon";
import {
	ComposerSuggestionGroup,
	ComposerSuggestionGroupHeading,
	ComposerSuggestionHeader,
	ComposerSuggestionItem,
	ComposerSuggestionList,
	ComposerSuggestionMessage,
	ComposerSuggestionSeparator,
	ComposerSuggestionSkeleton,
} from "@/components/new-chat/composer-suggestion-popup";
import {
	type ComposerSuggestionNavigatorRef,
	type ComposerSuggestionNode,
	useComposerSuggestionNavigator,
} from "@/components/new-chat/use-composer-suggestion-navigator";
import { Spinner } from "@/components/ui/spinner";
import type { Document, SearchDocumentTitlesResponse } from "@/contracts/types/document.types";
import { useArtifactsByDocument } from "@/features/artifacts/hooks/use-artifacts-by-document";
import { documentsApiService } from "@/lib/apis/documents-api.service";
import { getMentionDocKey } from "@/lib/chat/mention-doc-key";
import { searchThreads } from "@/lib/chat/thread-persistence";
import { queries } from "@/zero/queries";

export type DocumentMentionPickerRef = ComposerSuggestionNavigatorRef;

interface DocumentMentionPickerProps {
	workspaceId: number;
	onSelectionChange: (mentions: MentionedDocumentInfo[]) => void;
	onDone: () => void;
	initialSelectedDocuments?: MentionedDocumentInfo[];
	externalSearch?: string;
	/**
	 * Surface the "Chats" view so the user can reference other
	 * conversations. Off by default so non-chat callers (e.g. automation
	 * task inputs) keep their original doc/folder surface.
	 */
	enableChatMentions?: boolean;
	/** Active thread id, excluded so a chat can't reference itself. */
	currentChatId?: number | null;
}

const PAGE_SIZE = 20;
const MIN_SEARCH_LENGTH = 2;
const DEBOUNCE_MS = 100;
const RECENTS_LIMIT = 3;
const RECENTS_STORAGE_PREFIX = "surfsense:composer-mention-recents:v1:";

type BrowseView = { kind: "root" } | { kind: "files-folders" } | { kind: "chats" };

type ResourceNodeValue =
	| { kind: "view"; view: BrowseView }
	| { kind: "mention"; mention: MentionedDocumentInfo };

function isMentionedContextItem(value: unknown): value is MentionedDocumentInfo {
	if (!value || typeof value !== "object") return false;
	const item = value as Partial<MentionedDocumentInfo>;
	if (typeof item.id !== "number" || typeof item.title !== "string") return false;
	if (item.kind === "doc") return typeof item.document_type === "string";
	if (item.kind === "folder") return true;
	if (item.kind === "thread") return true;
	return false;
}

function getRecentsStorageKey(workspaceId: number) {
	return `${RECENTS_STORAGE_PREFIX}${workspaceId}`;
}

function readRecentMentions(workspaceId: number): MentionedDocumentInfo[] {
	if (typeof window === "undefined") return [];
	try {
		const raw = window.localStorage.getItem(getRecentsStorageKey(workspaceId));
		if (!raw) return [];
		const parsed: unknown = JSON.parse(raw);
		if (!Array.isArray(parsed)) return [];
		return parsed.filter(isMentionedContextItem).slice(0, RECENTS_LIMIT);
	} catch {
		return [];
	}
}

function writeRecentMentions(workspaceId: number, mentions: MentionedDocumentInfo[]) {
	if (typeof window === "undefined") return;
	try {
		window.localStorage.setItem(
			getRecentsStorageKey(workspaceId),
			JSON.stringify(mentions.slice(0, RECENTS_LIMIT))
		);
	} catch {
		// Recents are optional UI state; storage failures should not block mention insertion.
	}
}

export function promoteRecentMention(workspaceId: number, mention: MentionedDocumentInfo) {
	const mentionKey = getMentionDocKey(mention);
	const next = [
		mention,
		...readRecentMentions(workspaceId).filter((item) => getMentionDocKey(item) !== mentionKey),
	].slice(0, RECENTS_LIMIT);
	writeRecentMentions(workspaceId, next);
	return next;
}

function getMentionIcon(mention: MentionedDocumentInfo, artifactFormat?: string) {
	return <MentionIcon mention={mention} artifactFormat={artifactFormat} className="size-4" />;
}

function refreshRecentMention(
	mention: MentionedDocumentInfo,
	documents: Pick<Document, "id" | "title" | "document_type">[],
	folders: { id: number; name: string }[],
	hasHydratedRecentDocs: boolean
): MentionedDocumentInfo | null {
	if (mention.kind === "doc") {
		const doc = documents.find(
			(item) => item.id === mention.id && item.document_type === mention.document_type
		);
		if (doc) return makeDocMention(doc);
		return hasHydratedRecentDocs ? null : mention;
	}
	if (mention.kind === "folder") {
		const folder = folders.find((item) => item.id === mention.id);
		return folder ? makeFolderMention({ id: folder.id, title: folder.name }) : null;
	}
	if (mention.kind === "thread") {
		// Threads aren't in the doc/folder lists; keep the
		// recent as-is (validated against the live thread search instead).
		return mention;
	}
	// Connector mentions (removed with the connectors feature) are dropped.
	return null;
}

function useDebounced<T>(value: T, delay = DEBOUNCE_MS) {
	const [debounced, setDebounced] = useState(value);
	const timeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

	useEffect(() => {
		if (timeoutRef.current) clearTimeout(timeoutRef.current);
		timeoutRef.current = setTimeout(() => setDebounced(value), delay);
		return () => {
			if (timeoutRef.current) clearTimeout(timeoutRef.current);
		};
	}, [value, delay]);

	return debounced;
}

function makeDocMention(
	doc: Pick<Document, "id" | "title" | "document_type">
): MentionedDocumentInfo {
	return {
		id: doc.id,
		title: doc.title,
		document_type: doc.document_type,
		kind: "doc",
	};
}

function makeFolderMention(folder: {
	id: number;
	title: string;
}): Extract<MentionedDocumentInfo, { kind: "folder" }> {
	return {
		id: folder.id,
		title: folder.title,
		kind: "folder",
	};
}

function makeThreadMentions(
	threads: { id: number; title: string }[],
	currentChatId?: number | null
): Extract<MentionedDocumentInfo, { kind: "thread" }>[] {
	return threads
		.filter((thread) => thread.id !== currentChatId)
		.map((thread) => makeThreadMention({ id: thread.id, title: thread.title }))
		.filter(
			(mention): mention is Extract<MentionedDocumentInfo, { kind: "thread" }> =>
				mention.kind === "thread"
		);
}

export const DocumentMentionPicker = forwardRef<
	DocumentMentionPickerRef,
	DocumentMentionPickerProps
>(function DocumentMentionPicker(
	{
		workspaceId,
		onSelectionChange,
		onDone,
		initialSelectedDocuments = [],
		externalSearch = "",
		enableChatMentions = false,
		currentChatId = null,
	},
	ref
) {
	const search = externalSearch;
	const debouncedSearch = useDebounced(search, DEBOUNCE_MS);
	const deferredSearch = useDeferredValue(debouncedSearch);
	const hasSearch = debouncedSearch.trim().length > 0;
	const isSearchValid = debouncedSearch.trim().length >= MIN_SEARCH_LENGTH;
	const isSingleCharSearch = debouncedSearch.trim().length === 1;
	const [view, setView] = useState<BrowseView>({ kind: "root" });

	const [accumulatedDocuments, setAccumulatedDocuments] = useState<
		Pick<Document, "id" | "title" | "document_type">[]
	>([]);
	const [currentPage, setCurrentPage] = useState(0);
	const [hasMore, setHasMore] = useState(false);
	const [isLoadingMore, setIsLoadingMore] = useState(false);
	const [recentMentions, setRecentMentions] = useState<MentionedDocumentInfo[]>(() =>
		readRecentMentions(workspaceId)
	);

	const [zeroFolders] = useZeroQuery(queries.folders.bySpace({ workspaceId }));
	const artifactsByDocument = useArtifactsByDocument(workspaceId);
	const paginationScopeKey = useMemo(
		() => `${workspaceId}:${debouncedSearch}`,
		[workspaceId, debouncedSearch]
	);
	const previousPaginationScopeKeyRef = useRef<string | null>(null);

	// Reset pagination state when the active search scope changes.
	useEffect(() => {
		if (previousPaginationScopeKeyRef.current === paginationScopeKey) return;
		previousPaginationScopeKeyRef.current = paginationScopeKey;
		setCurrentPage(0);
		setHasMore(false);
	}, [paginationScopeKey]);

	useEffect(() => {
		if (hasSearch) setView({ kind: "root" });
	}, [hasSearch]);

	useEffect(() => {
		setRecentMentions(readRecentMentions(workspaceId));
	}, [workspaceId]);

	const titleSearchParams = useMemo(
		() => ({
			workspace_id: workspaceId,
			page: 0,
			page_size: PAGE_SIZE,
			...(isSearchValid ? { title: debouncedSearch.trim() } : {}),
		}),
		[workspaceId, debouncedSearch, isSearchValid]
	);

	const { data: titleSearchResults, isLoading: isTitleSearchLoading } = useQuery({
		queryKey: ["document-titles", titleSearchParams],
		queryFn: ({ signal }) =>
			documentsApiService.searchDocumentTitles({ queryParams: titleSearchParams }, signal),
		staleTime: 60 * 1000,
		enabled: !!workspaceId && currentPage === 0 && (!hasSearch || isSearchValid),
		placeholderData: keepPreviousData,
	});

	const filterBySearchTerm = useCallback(
		(docs: Pick<Document, "id" | "title" | "document_type">[]) => {
			if (!isSearchValid) return docs;
			const searchLower = debouncedSearch.trim().toLowerCase();
			return docs.filter((doc) => doc.title.toLowerCase().includes(searchLower));
		},
		[debouncedSearch, isSearchValid]
	);

	useEffect(() => {
		if (currentPage !== 0) return;
		const combinedDocs: Pick<Document, "id" | "title" | "document_type">[] = [];

		if (titleSearchResults?.items) {
			combinedDocs.push(...titleSearchResults.items);
			setHasMore(titleSearchResults.has_more);
		}

		setAccumulatedDocuments(filterBySearchTerm(combinedDocs));
	}, [titleSearchResults, currentPage, filterBySearchTerm]);

	const loadNextPage = useCallback(async () => {
		if (isLoadingMore || !hasMore) return;

		const nextPage = currentPage + 1;
		setIsLoadingMore(true);

		try {
			const queryParams = {
				workspace_id: workspaceId,
				page: nextPage,
				page_size: PAGE_SIZE,
				...(isSearchValid ? { title: debouncedSearch.trim() } : {}),
			};
			const response: SearchDocumentTitlesResponse = await documentsApiService.searchDocumentTitles(
				{
					queryParams,
				}
			);

			setAccumulatedDocuments((prev) => [...prev, ...response.items]);
			setHasMore(response.has_more);
			setCurrentPage(nextPage);
		} catch (error) {
			console.error("Failed to load next page:", error);
		} finally {
			setIsLoadingMore(false);
		}
	}, [currentPage, hasMore, isLoadingMore, debouncedSearch, workspaceId, isSearchValid]);

	const actualDocuments = useMemo(() => {
		if (!isSingleCharSearch) return accumulatedDocuments;
		const searchLower = deferredSearch.trim().toLowerCase();
		return accumulatedDocuments.filter((doc) => doc.title.toLowerCase().includes(searchLower));
	}, [accumulatedDocuments, deferredSearch, isSingleCharSearch]);

	const folderMentions = useMemo(() => {
		const all = (zeroFolders ?? []).map((f) => makeFolderMention({ id: f.id, title: f.name }));
		if (!hasSearch) return all;
		const needle = (isSingleCharSearch ? deferredSearch : debouncedSearch).trim().toLowerCase();
		if (!needle) return all;
		return all.filter((f) => f.title.toLowerCase().includes(needle));
	}, [zeroFolders, debouncedSearch, deferredSearch, isSingleCharSearch, hasSearch]);

	// Threads are fetched on demand: when the user opens the Chats view
	// or types a search. An empty title returns recent threads (the
	// backend ``ilike '%%'`` matches all, newest first).
	const { data: threadResults = [], isLoading: isThreadsLoading } = useQuery({
		queryKey: ["composer-mention-threads", workspaceId, debouncedSearch],
		queryFn: () => searchThreads(workspaceId, debouncedSearch.trim()),
		staleTime: 60 * 1000,
		enabled: enableChatMentions && !!workspaceId && (view.kind === "chats" || hasSearch),
		placeholderData: keepPreviousData,
	});
	const threadMentions = useMemo(
		() => (enableChatMentions ? makeThreadMentions(threadResults, currentChatId) : []),
		[enableChatMentions, threadResults, currentChatId]
	);
	const recentDocMentions = useMemo(
		() => recentMentions.filter((mention) => mention.kind === "doc"),
		[recentMentions]
	);
	const recentDocIdsKey = useMemo(
		() => recentDocMentions.map((mention) => mention.id).join(","),
		[recentDocMentions]
	);
	const { data: hydratedRecentDocs = [], isFetched: hasHydratedRecentDocs } = useQuery({
		queryKey: ["composer-mention-recent-docs", workspaceId, recentDocIdsKey],
		queryFn: async () => {
			const results = await Promise.allSettled(
				recentDocMentions.map((mention) => documentsApiService.getDocument({ id: mention.id }))
			);
			return results
				.map((result) => (result.status === "fulfilled" ? result.value : null))
				.filter((doc): doc is Document => doc !== null);
		},
		enabled: recentDocMentions.length > 0,
		staleTime: 60 * 1000,
	});
	const recentValidationDocuments = useMemo(
		() => [...actualDocuments, ...hydratedRecentDocs],
		[actualDocuments, hydratedRecentDocs]
	);
	const visibleRecentMentions = useMemo(
		() =>
			recentMentions
				.map((mention) =>
					refreshRecentMention(
						mention,
						recentValidationDocuments,
						zeroFolders ?? [],
						hasHydratedRecentDocs
					)
				)
				.filter((mention): mention is MentionedDocumentInfo => mention !== null)
				.slice(0, RECENTS_LIMIT),
		[hasHydratedRecentDocs, recentMentions, recentValidationDocuments, zeroFolders]
	);

	const selectedKeys = useMemo(
		() => new Set(initialSelectedDocuments.map((d) => getMentionDocKey(d))),
		[initialSelectedDocuments]
	);

	const selectMention = useCallback(
		(mention: MentionedDocumentInfo) => {
			onSelectionChange([...initialSelectedDocuments, mention]);
			onDone();
		},
		[initialSelectedDocuments, onSelectionChange, onDone]
	);
	const recentRootNodes = useMemo<ComposerSuggestionNode<ResourceNodeValue>[]>(
		() =>
			visibleRecentMentions.map((mention) => ({
				id: `recent:${getMentionDocKey(mention)}`,
				label: mention.title,
				icon: getMentionIcon(
					mention,
					mention.kind === "doc" ? artifactsByDocument.get(mention.id)?.format : undefined
				),
				type: "item" as const,
				disabled: selectedKeys.has(getMentionDocKey(mention)),
				value: { kind: "mention" as const, mention },
			})),
		[artifactsByDocument, visibleRecentMentions, selectedKeys]
	);

	const rootNodes = useMemo<ComposerSuggestionNode<ResourceNodeValue>[]>(() => {
		const nodes: ComposerSuggestionNode<ResourceNodeValue>[] = [...recentRootNodes];
		nodes.push({
			id: "files-folders",
			label: "Files & Folders",
			subtitle: "Browse your knowledge base",
			icon: <Files className="size-4" />,
			type: "branch",
			value: { kind: "view", view: { kind: "files-folders" } },
		});
		if (enableChatMentions) {
			nodes.push({
				id: "chats",
				label: "Chats",
				subtitle: "Reference another conversation",
				icon: <MessageCircleMore className="size-4" />,
				type: "branch",
				value: { kind: "view", view: { kind: "chats" } },
			});
		}
		return nodes;
	}, [enableChatMentions, recentRootNodes]);

	const docNodes = useMemo<ComposerSuggestionNode<ResourceNodeValue>[]>(
		() =>
			actualDocuments.map((doc) => {
				const mention = makeDocMention(doc);
				return {
					id: getMentionDocKey(mention),
					label: doc.title,
					icon: getMentionIcon(mention, artifactsByDocument.get(doc.id)?.format),
					type: "item" as const,
					disabled: selectedKeys.has(getMentionDocKey(mention)),
					value: { kind: "mention" as const, mention },
				};
			}),
		[actualDocuments, artifactsByDocument, selectedKeys]
	);

	const folderNodes = useMemo<ComposerSuggestionNode<ResourceNodeValue>[]>(
		() =>
			folderMentions.map((mention) => ({
				id: getMentionDocKey(mention),
				label: mention.title,
				subtitle: "Folder",
				icon: getMentionIcon(mention),
				type: "item" as const,
				disabled: selectedKeys.has(getMentionDocKey(mention)),
				value: { kind: "mention" as const, mention },
			})),
		[folderMentions, selectedKeys]
	);

	const threadNodes = useMemo<ComposerSuggestionNode<ResourceNodeValue>[]>(
		() =>
			threadMentions.map((mention) => ({
				id: getMentionDocKey(mention),
				label: mention.title,
				subtitle: "Chat",
				icon: getMentionIcon(mention),
				type: "item" as const,
				disabled: selectedKeys.has(getMentionDocKey(mention)),
				value: { kind: "mention" as const, mention },
			})),
		[selectedKeys, threadMentions]
	);

	const searchNodes = useMemo(
		() => [...docNodes, ...folderNodes, ...threadNodes],
		[docNodes, folderNodes, threadNodes]
	);

	const browseNodes = useMemo<ComposerSuggestionNode<ResourceNodeValue>[]>(() => {
		if (view.kind === "root") return rootNodes;
		if (view.kind === "files-folders") return [...folderNodes, ...docNodes];
		if (view.kind === "chats") return threadNodes;
		return [];
	}, [docNodes, folderNodes, rootNodes, threadNodes, view]);

	const visibleNodes = hasSearch ? searchNodes : browseNodes;
	const handleNodeSelect = useCallback(
		(node: ComposerSuggestionNode<ResourceNodeValue>) => {
			const value = node.value;
			if (!value) return;
			if (value.kind === "view") {
				setView(value.view);
				return;
			}
			selectMention(value.mention);
		},
		[selectMention]
	);
	const handleBack = useCallback(() => {
		if (hasSearch || view.kind === "root") return false;
		setView({ kind: "root" });
		return true;
	}, [hasSearch, view]);

	const navigator = useComposerSuggestionNavigator({
		nodes: visibleNodes,
		onSelect: handleNodeSelect,
		onBack: handleBack,
		ref,
	});
	const canLoadMoreDocuments = hasSearch || view.kind === "files-folders";

	const handleScroll = useCallback(
		(e: UIEvent<HTMLDivElement>) => {
			if (!canLoadMoreDocuments) return;
			const target = e.currentTarget;
			const scrollBottom = target.scrollHeight - target.scrollTop - target.clientHeight;

			if (scrollBottom < 50 && hasMore && !isLoadingMore) {
				loadNextPage();
			}
		},
		[canLoadMoreDocuments, hasMore, isLoadingMore, loadNextPage]
	);

	const isRootBrowseView = !hasSearch && view.kind === "root";
	const isVisibleViewLoading = hasSearch
		? isTitleSearchLoading || isThreadsLoading
		: view.kind === "files-folders"
			? isTitleSearchLoading
			: view.kind === "chats"
				? isThreadsLoading
				: false;
	const actualLoading =
		isVisibleViewLoading && !isSingleCharSearch && visibleNodes.length === 0 && !isRootBrowseView;

	const title =
		hasSearch || view.kind === "root"
			? null
			: view.kind === "files-folders"
				? "Files & Folders"
				: "Chats";

	return (
		<ComposerSuggestionList
			ref={navigator.scrollContainerRef}
			onScroll={handleScroll}
			role="listbox"
			tabIndex={-1}
			className={isRootBrowseView ? "max-h-none overflow-visible sm:max-h-none" : undefined}
		>
			{actualLoading ? (
				<ComposerSuggestionSkeleton rows={8} mobileRows={8} />
			) : (
				<ComposerSuggestionGroup>
					{title ? (
						<>
							<ComposerSuggestionHeader
								role="button"
								tabIndex={0}
								aria-label={`Back from ${title}`}
								onClick={handleBack}
								onKeyDown={(event) => {
									if (event.key === "Enter" || event.key === " ") {
										event.preventDefault();
										handleBack();
									}
								}}
								className="cursor-pointer rounded-sm transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
								icon={
									<span className="-ml-0.5 flex size-4.5 items-center justify-center">
										<ChevronLeft className="size-3.5" />
									</span>
								}
							>
								<span className="flex-1 truncate">{title}</span>
							</ComposerSuggestionHeader>
							<ComposerSuggestionSeparator />
						</>
					) : null}

					{visibleNodes.length > 0 ? (
						<>
							{hasSearch ? (
								<ComposerSuggestionGroupHeading>Suggested Context</ComposerSuggestionGroupHeading>
							) : null}
							{!hasSearch && view.kind === "root" && recentRootNodes.length > 0 ? (
								<ComposerSuggestionGroupHeading>Recents</ComposerSuggestionGroupHeading>
							) : null}
							{visibleNodes.map((node, index) => {
								const showRecentsSeparator =
									!hasSearch &&
									view.kind === "root" &&
									recentRootNodes.length > 0 &&
									index === recentRootNodes.length;
								return (
									<Fragment key={node.id}>
										{showRecentsSeparator ? <ComposerSuggestionSeparator /> : null}
										<ComposerSuggestionItem
											ref={navigator.getItemRef(index)}
											icon={node.icon}
											selected={index === navigator.highlightedIndex}
											disabled={node.disabled}
											onClick={() => !node.disabled && handleNodeSelect(node)}
											onMouseEnter={() => navigator.setHighlightedIndex(index)}
										>
											<span className="min-w-0 flex-1">
												<span className="block truncate text-xs" title={node.label}>
													{node.label}
												</span>
												{node.subtitle ? (
													<span className="block truncate text-[10px] text-muted-foreground">
														{node.subtitle}
													</span>
												) : null}
											</span>
											{node.type === "branch" ? (
												<ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
											) : null}
										</ComposerSuggestionItem>
									</Fragment>
								);
							})}
						</>
					) : (
						<ComposerSuggestionMessage>
							{hasSearch ? "No matching context" : "No items available"}
						</ComposerSuggestionMessage>
					)}

					{canLoadMoreDocuments && isLoadingMore && (
						<div className="flex items-center justify-center py-2 text-primary">
							<Spinner size="sm" />
						</div>
					)}
				</ComposerSuggestionGroup>
			)}
		</ComposerSuggestionList>
	);
});
