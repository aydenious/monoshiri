"use client";

import { useAtom } from "jotai";
import { Check, ChevronDown, Search, SlidersHorizontal } from "lucide-react";
import { useRouter } from "next/navigation";
import type { ComponentProps, ReactNode, UIEvent } from "react";
import { useCallback, useMemo, useState } from "react";
import {
	globalModelConnectionsAtom,
	modelConnectionsAtom,
} from "@/atoms/model-connections/model-connections-query.atoms";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Drawer,
	DrawerContent,
	DrawerHandle,
	DrawerHeader,
	DrawerTitle,
	DrawerTrigger,
} from "@/components/ui/drawer";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import type { ConnectionRead, ModelRead } from "@/contracts/types/model-connections.types";
import { useIsMobile } from "@/hooks/use-mobile";
import { AUTO_PROVIDER_ICON_KEY, getProviderIcon } from "@/lib/provider-icons";
import { cn } from "@/lib/utils";
import { providerDisplay } from "../settings/model-connections/provider-metadata";

export const AUTO_MODEL_ID = 0;

type ModelWithConnection = ModelRead & {
	connectionId: number;
	connectionLabel: string;
	connectionScope: string;
	provider: string;
};

type SupportedModelField = "supports_chat" | "supports_image_generation";
type ScrollPosition = "top" | "middle" | "bottom";
type ModelGroups = Record<string, ModelWithConnection[]>;

function connectionLabel(connection: ConnectionRead) {
	if (connection.scope === "GLOBAL") return "Global";
	return providerDisplay(connection.provider).name;
}

function flattenModels(connections: ConnectionRead[], supportedField: SupportedModelField) {
	return connections.flatMap((connection) =>
		connection.models
			.filter((model) => model.enabled && Boolean(model[supportedField]))
			.map((model) => ({
				...model,
				connectionId: connection.id,
				connectionLabel: connectionLabel(connection),
				connectionScope: connection.scope,
				provider: connection.provider,
			}))
	);
}

function isFreeGlobalModel(model: ModelWithConnection) {
	return model.connectionScope === "GLOBAL" && model.billing_tier?.toLowerCase() === "free";
}

function modelName(model: ModelWithConnection) {
	const name = model.display_name || model.model_id;
	if (model.connectionScope === "GLOBAL") {
		return name.replace(/\s+\(free\)$/i, "");
	}
	return name;
}

function filterModels(models: ModelWithConnection[], search: string) {
	const normalized = search.trim().toLowerCase();
	if (!normalized) return models;
	return models.filter((model) =>
		[modelName(model), model.model_id, model.connectionLabel]
			.join(" ")
			.toLowerCase()
			.includes(normalized)
	);
}

function groupedModels(models: ModelWithConnection[]) {
	return models.reduce<ModelGroups>((groups, model) => {
		const key = model.connectionLabel;
		if (!groups[key]) groups[key] = [];
		groups[key].push(model);
		return groups;
	}, {});
}

/**
 * Shared state and data for the chat and image model selectors. Everything the two selectors
 * disagree on (the role key they write, labels, extra callbacks) stays in the caller.
 */
export function useModelSelector(workspaceId: number, supportedField: SupportedModelField) {
	const router = useRouter();
	const isMobile = useIsMobile();
	const [open, setOpen] = useState(false);
	const [search, setSearch] = useState("");
	const [scrollPos, setScrollPos] = useState<ScrollPosition>("top");
	const [{ data: globalConnections = [], isLoading: globalLoading }] = useAtom(
		globalModelConnectionsAtom
	);
	const [{ data: connections = [], isLoading: connectionsLoading }] = useAtom(modelConnectionsAtom);

	const allConnections = useMemo(
		() => [...globalConnections, ...connections],
		[globalConnections, connections]
	);
	const allModels = useMemo(
		() => flattenModels(allConnections, supportedField),
		[allConnections, supportedField]
	);
	const visibleModels = useMemo(() => filterModels(allModels, search), [allModels, search]);
	const groups = useMemo(() => groupedModels(visibleModels), [visibleModels]);
	const loading = globalLoading || connectionsLoading;
	const hasSearchQuery = search.trim().length > 0;

	function handleOpenChange(nextOpen: boolean) {
		if (!nextOpen) setSearch("");
		setOpen(nextOpen);
	}

	function closeSelector() {
		setSearch("");
		setOpen(false);
	}

	function manageModelConnections() {
		setOpen(false);
		router.push(`/dashboard/${workspaceId}/workspace-settings/models`);
	}

	const handleScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
		const el = event.currentTarget;
		const atTop = el.scrollTop <= 2;
		const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 2;
		setScrollPos(atTop ? "top" : atBottom ? "bottom" : "middle");
	}, []);

	return {
		allModels,
		closeSelector,
		groups,
		handleOpenChange,
		handleScroll,
		hasSearchQuery,
		isMobile,
		loading,
		manageModelConnections,
		open,
		scrollPos,
		search,
		setSearch,
	};
}

const ITEM_CLASS =
	"flex w-full items-center justify-between rounded-md px-3 py-2 text-left transition-colors hover:bg-accent hover:text-accent-foreground";

function AutoModelRow({ selected, onSelect }: { selected: boolean; onSelect: () => void }) {
	return (
		<button type="button" className={ITEM_CLASS} onClick={onSelect}>
			<div className="min-w-0 flex-1">
				<div className="flex min-w-0 items-center gap-2 font-medium">
					{getProviderIcon(AUTO_PROVIDER_ICON_KEY, { className: "size-4 shrink-0" })}
					<span className="truncate">Auto</span>
				</div>
			</div>
			{selected ? <Check className="h-4 w-4" /> : null}
		</button>
	);
}

function ModelRow({
	model,
	selected,
	onSelect,
}: {
	model: ModelWithConnection;
	selected: boolean;
	onSelect: () => void;
}) {
	return (
		<button type="button" className={ITEM_CLASS} onClick={onSelect}>
			<div className="min-w-0 flex-1">
				<div className="flex min-w-0 items-center gap-2 font-medium">
					{getProviderIcon(model.provider, { className: "size-4 shrink-0" })}
					<span className="truncate">{modelName(model)}</span>
				</div>
			</div>
			<div className="ml-3 flex shrink-0 items-center gap-2">
				{isFreeGlobalModel(model) ? (
					<Badge
						variant="secondary"
						className="h-5 shrink-0 rounded-sm border-0 bg-popover-foreground/10 px-1.5 text-[11px] text-popover-foreground hover:bg-popover-foreground/10"
					>
						Free
					</Badge>
				) : null}
				{selected ? <Check className="h-4 w-4" /> : null}
			</div>
		</button>
	);
}

function ModelGroup({
	connection,
	models,
	selectedModelId,
	onSelect,
}: {
	connection: string;
	models: ModelWithConnection[];
	selectedModelId: number | null | undefined;
	onSelect: (modelId: number) => void;
}) {
	return (
		<div className="mt-3">
			<div className="px-2 py-1 text-sm font-semibold text-muted-foreground">{connection}</div>
			{models.map((model) => (
				<ModelRow
					key={model.id}
					model={model}
					selected={selectedModelId === model.id}
					onSelect={() => onSelect(model.id)}
				/>
			))}
		</div>
	);
}

function ModelSearch({
	value,
	onChange,
	placeholder,
}: {
	value: string;
	onChange: (value: string) => void;
	placeholder: string;
}) {
	return (
		<div className="p-2">
			<div className="relative">
				<Search className="absolute left-0.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
				<Input
					value={value}
					onChange={(event) => onChange(event.target.value)}
					placeholder={placeholder}
					className="h-8 border-0 bg-transparent pl-6 text-sm shadow-none"
				/>
			</div>
		</div>
	);
}

function ModelScrollArea({
	children,
	scrollPos,
	onScroll,
}: {
	children: ReactNode;
	scrollPos: ScrollPosition;
	onScroll: (event: UIEvent<HTMLDivElement>) => void;
}) {
	const mask = `linear-gradient(to bottom, ${scrollPos === "top" ? "black" : "transparent"}, black 16px, black calc(100% - 16px), ${scrollPos === "bottom" ? "black" : "transparent"})`;
	return (
		<div
			className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-1.5 py-1.5"
			onScroll={onScroll}
			style={{ maskImage: mask, WebkitMaskImage: mask }}
		>
			{children}
		</div>
	);
}

function ModelEmptyState({
	hasSearchQuery,
	searchEmptyText,
	emptyText,
}: {
	hasSearchQuery: boolean;
	searchEmptyText: string;
	emptyText: string;
}) {
	return (
		<div className="px-3 py-8 text-center text-sm text-muted-foreground">
			{hasSearchQuery ? searchEmptyText : emptyText}
		</div>
	);
}

function ModelFooter({ onManageModels }: { onManageModels: () => void }) {
	return (
		<div className="p-2">
			<Button
				variant="ghost"
				className="w-full justify-start rounded-md bg-foreground/5 hover:bg-foreground/10 hover:text-foreground"
				onClick={onManageModels}
			>
				<SlidersHorizontal className="h-4 w-4" /> Manage models
			</Button>
		</div>
	);
}

export function ModelSelectorContent({
	autoSelected,
	emptyText,
	groups,
	hasSearchQuery,
	loading,
	onManageModels,
	onScroll,
	onSearchChange,
	onSelect,
	scrollPos,
	search,
	searchEmptyText,
	searchPlaceholder,
	selectedModelId,
}: {
	autoSelected: boolean;
	emptyText: string;
	groups: ModelGroups;
	hasSearchQuery: boolean;
	loading: boolean;
	onManageModels: () => void;
	onScroll: (event: UIEvent<HTMLDivElement>) => void;
	onSearchChange: (value: string) => void;
	onSelect: (modelId: number) => void;
	scrollPos: ScrollPosition;
	search: string;
	searchEmptyText: string;
	searchPlaceholder: string;
	selectedModelId: number | null | undefined;
}) {
	return (
		<div className="flex h-[320px] select-none flex-col overflow-hidden">
			<ModelSearch value={search} onChange={onSearchChange} placeholder={searchPlaceholder} />
			<ModelScrollArea scrollPos={scrollPos} onScroll={onScroll}>
				<AutoModelRow selected={autoSelected} onSelect={() => onSelect(AUTO_MODEL_ID)} />
				{loading ? (
					<div className="flex items-center justify-center py-8">
						<Spinner />
					</div>
				) : Object.keys(groups).length === 0 ? (
					<ModelEmptyState
						hasSearchQuery={hasSearchQuery}
						searchEmptyText={searchEmptyText}
						emptyText={emptyText}
					/>
				) : (
					Object.entries(groups).map(([connection, models]) => (
						<ModelGroup
							key={connection}
							connection={connection}
							models={models}
							selectedModelId={selectedModelId}
							onSelect={onSelect}
						/>
					))
				)}
			</ModelScrollArea>
			<ModelFooter onManageModels={onManageModels} />
		</div>
	);
}

// Rendered under a Radix `asChild` trigger: the Slot props (onClick, aria-expanded,
// data-state, ref) must reach the Button, or the selector never opens.
export function ModelSelectorTrigger({
	ariaLabel,
	className,
	selected,
	showIconOnly,
	...slotProps
}: ComponentProps<typeof Button> & {
	ariaLabel: string;
	selected: ModelWithConnection | undefined;
	showIconOnly: boolean;
}) {
	return (
		<Button
			{...slotProps}
			type="button"
			variant="ghost"
			size="sm"
			data-desktop-layout-overflow-boundary
			aria-label={ariaLabel}
			className={cn(
				"h-8 min-w-0 gap-2 rounded-md px-3 text-muted-foreground transition-colors",
				"select-none",
				"hover:bg-foreground/10 hover:text-foreground",
				"data-[state=open]:bg-foreground/10 data-[state=open]:text-foreground",
				className,
				showIconOnly && "h-9 w-auto shrink-0 justify-center gap-1 px-2"
			)}
		>
			{selected
				? getProviderIcon(selected.provider, { className: "size-4 shrink-0" })
				: getProviderIcon(AUTO_PROVIDER_ICON_KEY, { className: "size-4 shrink-0" })}
			{showIconOnly ? null : (
				<span className="min-w-0 max-w-40 flex-1 truncate text-sm">
					{selected ? modelName(selected) : "Auto"}
				</span>
			)}
			<ChevronDown className="h-3.5 w-3.5 shrink-0" />
		</Button>
	);
}

export function ModelSelectorShell({
	children,
	drawerTitle,
	isMobile,
	onOpenChange,
	open,
	trigger,
}: {
	children: ReactNode;
	drawerTitle: string;
	isMobile: boolean;
	onOpenChange: (open: boolean) => void;
	open: boolean;
	trigger: ReactNode;
}) {
	if (isMobile) {
		return (
			<Drawer open={open} onOpenChange={onOpenChange}>
				<DrawerTrigger asChild>{trigger}</DrawerTrigger>
				<DrawerContent className="max-h-[85vh]">
					<DrawerHandle />
					<DrawerHeader>
						<DrawerTitle>{drawerTitle}</DrawerTitle>
					</DrawerHeader>
					{children}
				</DrawerContent>
			</Drawer>
		);
	}

	return (
		<Popover open={open} onOpenChange={onOpenChange}>
			<PopoverTrigger asChild>{trigger}</PopoverTrigger>
			<PopoverContent
				align="start"
				className="w-[340px] border border-popover-border bg-popover p-0 text-popover-foreground shadow-md"
			>
				{children}
			</PopoverContent>
		</Popover>
	);
}
