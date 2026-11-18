"use client";

import { MessageCircleMore, SquarePen } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { SIDEBAR_MIN_WIDTH } from "../../sidebar-preferences";
import type { SidebarContentProps } from "../../types/layout.types";
import { ChatListItem } from "./ChatListItem";
import { NavSection } from "./NavSection";
import { SidebarButton } from "./SidebarButton";
import { SidebarCollapseButton } from "./SidebarCollapseButton";
import { SidebarHeader } from "./SidebarHeader";
import { SidebarSection } from "./SidebarSection";
import { SidebarUserProfile } from "./SidebarUserProfile";

const CHAT_LIST_SKELETON_WIDTHS = ["w-[78%]", "w-[64%]", "w-[86%]", "w-[58%]", "w-[72%]"];

function ChatListItemSkeleton({ widthClass }: { widthClass: string }) {
	return (
		<div className="group/item relative w-full">
			<div className="flex h-[32px] w-full items-center rounded-md px-2 py-1.5">
				<Skeleton className={cn("h-4 rounded", widthClass)} />
			</div>
		</div>
	);
}

function ChatListSkeletonRows() {
	return (
		<div className="flex flex-col gap-0.5">
			{CHAT_LIST_SKELETON_WIDTHS.map((widthClass) => (
				<ChatListItemSkeleton key={widthClass} widthClass={widthClass} />
			))}
		</div>
	);
}

interface SidebarProps extends SidebarContentProps {
	isCollapsed?: boolean;
	onToggleCollapse?: () => void;
	isAllChatsActive?: boolean;
	onNavigate?: () => void;
	className?: string;
	disableTooltips?: boolean;
	sidebarWidth?: number;
	isResizing?: boolean;
	renderUserProfile?: boolean;
}

export function Sidebar({
	workspace,
	isCollapsed = false,
	onToggleCollapse,
	navItems,
	onNavItemClick,
	chats,
	activeChatId,
	onNewChat,
	onChatSelect,
	onChatPrefetch,
	onChatRename,
	onChatDelete,
	onChatArchive,
	onChatsClick,
	onViewAllChats,
	isAllChatsActive = false,
	user,
	onSettings,
	onUserSettings,
	onLogout,
	theme,
	setTheme,
	className,
	isLoadingChats = false,
	disableTooltips = false,
	sidebarWidth = SIDEBAR_MIN_WIDTH,
	isResizing = false,
	renderUserProfile = true,
}: SidebarProps) {
	const t = useTranslations("sidebar");
	const [openDropdownChatId, setOpenDropdownChatId] = useState<number | null>(null);
	const [isSidebarNavScrolled, setIsSidebarNavScrolled] = useState(false);

	// Automations and Artifacts are rendered explicitly right below
	// New Chat. Pull them out of the nav items list so they don't also appear
	// in the bottom NavSection. Documents is only present in navItems on mobile,
	// where it opens the Documents workspace destination.
	const automationsItem = useMemo(
		() => navItems.find((item) => item.url.endsWith("/automations")),
		[navItems]
	);
	const artifactsItem = useMemo(
		() => navItems.find((item) => item.url.endsWith("/artifacts")),
		[navItems]
	);
	const documentsItem = useMemo(
		() => navItems.find((item) => item.url === "#documents" || item.url.endsWith("/documents")),
		[navItems]
	);
	const footerNavItems = useMemo(
		() =>
			navItems.filter(
				(item) =>
					item.url !== "#documents" &&
					!item.url.endsWith("/documents") &&
					!item.url.endsWith("/automations") &&
					!item.url.endsWith("/artifacts")
			),
		[navItems]
	);

	const collapsedWidth = 51;

	return (
		<div
			className={cn(
				"relative flex h-full flex-col bg-panel text-sidebar-foreground overflow-hidden select-none",
				!isResizing && "transition-[width] duration-200 ease-out",
				className
			)}
			style={{ width: isCollapsed ? collapsedWidth : sidebarWidth }}
		>
			<div className="relative flex h-12 shrink-0 items-center gap-0 px-1">
				<div
					className={cn(
						"min-w-0 overflow-hidden",
						"transition-[max-width,opacity,margin-right] duration-200 ease-out",
						isCollapsed ? "max-w-0 opacity-0 mr-0" : "max-w-[400px] flex-1 opacity-100"
					)}
					aria-hidden={isCollapsed}
				>
					<SidebarHeader workspace={workspace} isCollapsed={false} onSettings={onSettings} />
				</div>
				<div className={cn("shrink-0", isCollapsed && "mx-auto")}>
					<SidebarCollapseButton
						isCollapsed={isCollapsed}
						onToggle={onToggleCollapse ?? (() => {})}
						disableTooltip={disableTooltips}
					/>
				</div>
			</div>

			<div
				className={cn(
					"relative z-10 flex flex-col gap-0.5 pt-1.5 pb-0 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-gradient-to-b after:from-panel after:to-transparent after:transition-opacity",
					isSidebarNavScrolled ? "after:opacity-100" : "after:opacity-0"
				)}
			>
				<SidebarButton
					icon={SquarePen}
					label={t("new_chat")}
					onClick={onNewChat}
					isCollapsed={isCollapsed}
				/>
			</div>

			<div
				className="flex-1 w-full min-h-0 overflow-y-auto overflow-x-hidden scrollbar-thin scrollbar-thumb-muted-foreground/20 scrollbar-track-transparent"
				onScroll={(event) => setIsSidebarNavScrolled(event.currentTarget.scrollTop > 0)}
			>
				<div className="flex flex-col gap-0.5 pt-0.5 pb-1.5">
					{onChatsClick && (
						<SidebarButton
							icon={MessageCircleMore}
							label={t("chats") || "Chats"}
							onClick={onChatsClick}
							isCollapsed={isCollapsed}
							isActive={isAllChatsActive}
							tooltipContent={isCollapsed ? t("chats") || "Chats" : undefined}
						/>
					)}
					{automationsItem && (
						<SidebarButton
							icon={automationsItem.icon}
							label={automationsItem.title}
							onClick={() => onNavItemClick?.(automationsItem)}
							isCollapsed={isCollapsed}
							isActive={automationsItem.isActive}
							tooltipContent={isCollapsed ? automationsItem.title : undefined}
						/>
					)}
					{artifactsItem && (
						<SidebarButton
							icon={artifactsItem.icon}
							label={artifactsItem.title}
							onClick={() => onNavItemClick?.(artifactsItem)}
							isCollapsed={isCollapsed}
							isActive={artifactsItem.isActive}
							tooltipContent={isCollapsed ? artifactsItem.title : undefined}
						/>
					)}
					{documentsItem && (
						<SidebarButton
							icon={documentsItem.icon}
							label={documentsItem.title}
							onClick={() => onNavItemClick?.(documentsItem)}
							isCollapsed={isCollapsed}
							isActive={documentsItem.isActive}
							tooltipContent={isCollapsed ? documentsItem.title : undefined}
						/>
					)}
				</div>

				{/* Chat sections - fills available space */}
				{isCollapsed ? (
					<div className="w-full" />
				) : (
					<div className="flex flex-col gap-1 pt-2 w-full min-h-0">
						<SidebarSection
							title={t("recents")}
							defaultOpen={true}
							fillHeight={true}
							alwaysShowAction={!disableTooltips && isAllChatsActive}
							action={
								onViewAllChats ? (
									<Button
										type="button"
										variant="ghost"
										onClick={onViewAllChats}
										className="h-auto cursor-pointer whitespace-nowrap bg-transparent p-0 text-xs font-medium text-muted-foreground/60 transition-colors hover:bg-transparent hover:text-muted-foreground"
									>
										{!disableTooltips && isAllChatsActive ? t("hide") : t("show_all")}
									</Button>
								) : undefined
							}
						>
							{isLoadingChats ? (
								<ChatListSkeletonRows />
							) : chats.length > 0 ? (
								<div className="relative">
									<div className={`flex flex-col gap-0.5 ${chats.length > 4 ? "pb-2" : ""}`}>
										{chats.slice(0, 14).map((chat) => (
											<ChatListItem
												key={chat.id}
												name={chat.name}
												isActive={chat.id === activeChatId}
												archived={chat.archived}
												dropdownOpen={openDropdownChatId === chat.id}
												onDropdownOpenChange={(open) =>
													setOpenDropdownChatId(open ? chat.id : null)
												}
												onClick={() => onChatSelect(chat)}
												onPrefetch={() => onChatPrefetch?.(chat)}
												onRename={() => onChatRename?.(chat)}
												onArchive={() => onChatArchive?.(chat)}
												onDelete={() => onChatDelete?.(chat)}
											/>
										))}
									</div>
									{/* Gradient fade indicator when more than 4 items */}
									{chats.length > 4 && (
										<div className="pointer-events-none absolute bottom-0 left-0 right-0 h-5 bg-gradient-to-t from-sidebar/80 to-transparent" />
									)}
								</div>
							) : (
								<p className="px-2 py-1 text-sm text-muted-foreground/60">{t("no_chats")}</p>
							)}
						</SidebarSection>
					</div>
				)}
			</div>

			{/* Footer */}
			<div className="mt-auto border-t">
				{/* Platform navigation */}
				{footerNavItems.length > 0 && (
					<NavSection
						items={footerNavItems}
						onItemClick={onNavItemClick}
						isCollapsed={isCollapsed}
					/>
				)}

				{renderUserProfile && (
					<SidebarUserProfile
						user={user}
						onUserSettings={onUserSettings}
						onLogout={onLogout}
						isCollapsed={isCollapsed}
						theme={theme}
						setTheme={setTheme}
					/>
				)}
			</div>
		</div>
	);
}
