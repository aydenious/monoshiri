"use client";

import { useAtomValue } from "jotai";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import { activeTabIdAtom } from "@/atoms/tabs/tabs.atom";
import { Spinner } from "@/components/ui/spinner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useIsMobile } from "@/hooks/use-mobile";
import { type ResolvedTab, useResolvedTabs } from "@/hooks/use-resolved-tabs";
import { cn } from "@/lib/utils";
import { SidebarProvider, useSidebarState } from "../../hooks";
import { useSidebarResize } from "../../hooks/useSidebarResize";
import { SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH } from "../../sidebar-preferences";
import type { SidebarContentProps, Workspace } from "../../types/layout.types";
import { Header } from "../header";
import { IconRail } from "../icon-rail";
import { MobileDocumentsWorkspaceView } from "../right-panel/MobileDocumentsWorkspaceView";
import { RightPanel, RightPanelToggleButton, useRightPanelLayout } from "../right-panel/RightPanel";
import { MobileSidebar, MobileSidebarTrigger, Sidebar } from "../sidebar";
import type { NotificationsDropdownData } from "../sidebar/NotificationsDropdown";
import { TabBar } from "../tabs/TabBar";
import { WorkspacePanel } from "./WorkspacePanel";
import { hasHorizontalOverflow, shouldAutoCollapseSidebar, WorkspaceSplit } from "./WorkspaceSplit";

const DocumentTabContent = dynamic(
	() => import("../tabs/DocumentTabContent").then((m) => ({ default: m.DocumentTabContent })),
	{
		ssr: false,
		loading: () => (
			<div className="flex-1 flex items-center justify-center h-full">
				<Spinner size="lg" />
			</div>
		),
	}
);
const MobileArtifactDrawer = dynamic(
	() =>
		import("@/features/artifacts/ui/artifact-panel").then((m) => ({
			default: m.MobileArtifactDrawer,
		})),
	{ ssr: false }
);
const MobileDocumentViewerPanel = dynamic(
	() =>
		import("@/features/documents/viewer/document-viewer-panel").then((module) => ({
			default: module.MobileDocumentViewerPanel,
		})),
	{ ssr: false }
);

interface LayoutShellProps extends SidebarContentProps {
	workspaces: Workspace[];
	activeWorkspaceId: number | null;
	onWorkspaceSelect: (id: number) => void;
	onWorkspaceDelete?: (workspace: Workspace) => void;
	onWorkspaceSettings?: (workspace: Workspace) => void;
	onAddWorkspace: () => void;
	isAtWorkspaceLimit?: boolean;
	maxWorkspacesPerUser?: number;
	initialSidebarCollapsed: boolean;
	initialSidebarWidth: number;
	isChatPage?: boolean;
	isAllChatsPage?: boolean;
	showTabs?: boolean;
	useWorkspacePanel?: boolean;
	workspacePanelViewportClassName?: string;
	workspacePanelContentClassName?: string;
	children: React.ReactNode;
	className?: string;
	notifications?: NotificationsDropdownData;
	documentsPanel?: {
		open: boolean;
		onOpenChange: (open: boolean) => void;
	};
	/** Mobile layouts that open Documents from local state instead of a dashboard route. */
	mobileDocumentsWorkspaceFromState?: boolean;
	onTabSwitch?: (tab: ResolvedTab) => void;
	onTabPrefetch?: (tab: ResolvedTab) => void;
}

function MainContentPanel({
	isChatPage,
	onTabSwitch,
	onTabPrefetch,
	onNewChat,
	showTabs = true,
	children,
}: {
	isChatPage: boolean;
	onTabSwitch?: (tab: ResolvedTab) => void;
	onTabPrefetch?: (tab: ResolvedTab) => void;
	onNewChat?: () => void;
	showTabs?: boolean;
	children: React.ReactNode;
}) {
	if (!showTabs) {
		return <UntabbedMainContentPanel isChatPage={isChatPage}>{children}</UntabbedMainContentPanel>;
	}

	return (
		<TabbedMainContentPanel
			isChatPage={isChatPage}
			onTabSwitch={onTabSwitch}
			onTabPrefetch={onTabPrefetch}
			onNewChat={onNewChat}
		>
			{children}
		</TabbedMainContentPanel>
	);
}

function UntabbedMainContentPanel({
	isChatPage,
	children,
}: {
	isChatPage: boolean;
	children: React.ReactNode;
}) {
	return (
		<div className="relative isolate flex flex-1 flex-col min-w-0">
			<div className="relative flex flex-1 flex-col bg-panel overflow-hidden min-w-0">
				<Header />
				<div className={cn("min-w-0 flex-1", isChatPage ? "overflow-hidden" : "overflow-auto")}>
					{children}
				</div>
			</div>
		</div>
	);
}

function TabbedMainContentPanel({
	isChatPage,
	onTabSwitch,
	onTabPrefetch,
	onNewChat,
	children,
}: {
	isChatPage: boolean;
	onTabSwitch?: (tab: ResolvedTab) => void;
	onTabPrefetch?: (tab: ResolvedTab) => void;
	onNewChat?: () => void;
	children: React.ReactNode;
}) {
	const activeTabId = useAtomValue(activeTabIdAtom);
	const tabs = useResolvedTabs();
	const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? null;
	const isDocumentTab = activeTab?.type === "document";

	return (
		<div className="relative isolate flex flex-1 flex-col min-w-0">
			<TabBar
				onTabSwitch={onTabSwitch}
				onTabPrefetch={onTabPrefetch}
				onNewChat={onNewChat}
				rightActions={<div aria-hidden="true" className="h-8 w-8 shrink-0" />}
				className="min-w-0"
			/>
			<div className="relative flex flex-1 flex-col bg-panel overflow-hidden min-w-0">
				<Header />

				{isDocumentTab && activeTab.entityId && activeTab.workspaceId ? (
					<div className="flex-1 overflow-hidden">
						<DocumentTabContent
							key={activeTab.entityId}
							documentId={activeTab.entityId}
							workspaceId={activeTab.workspaceId}
							title={activeTab.title}
						/>
					</div>
				) : (
					<div className={cn("min-w-0 flex-1", isChatPage ? "overflow-hidden" : "overflow-auto")}>
						{children}
					</div>
				)}
			</div>
		</div>
	);
}

export function LayoutShell({
	workspaces,
	activeWorkspaceId,
	onWorkspaceSelect,
	onWorkspaceDelete,
	onWorkspaceSettings,
	onAddWorkspace,
	isAtWorkspaceLimit = false,
	maxWorkspacesPerUser,
	workspace,
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
	user,
	onSettings,
	onUserSettings,
	onLogout,
	pageUsage,
	theme,
	setTheme,
	initialSidebarCollapsed,
	initialSidebarWidth,
	isChatPage = false,
	isAllChatsPage = false,
	showTabs = true,
	useWorkspacePanel = false,
	workspacePanelViewportClassName,
	workspacePanelContentClassName,
	children,
	className,
	notifications,
	isLoadingChats = false,
	documentsPanel,
	mobileDocumentsWorkspaceFromState = false,
	onTabSwitch,
	onTabPrefetch,
}: LayoutShellProps) {
	const isMobile = useIsMobile();
	const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
	const {
		sidebarWidth,
		handlePointerDown: onResizePointerDown,
		isDragging: isResizing,
	} = useSidebarResize(initialSidebarWidth);
	const { isCollapsed, setIsCollapsed, toggleCollapsed } = useSidebarState(initialSidebarCollapsed);
	const rightPanelLayout = useRightPanelLayout(documentsPanel?.open ?? false);
	const desktopLayoutRef = useRef<HTMLDivElement>(null);
	const wasLayoutOverflowingRef = useRef(false);

	useEffect(() => {
		const desktopLayout = desktopLayoutRef.current;
		if (isMobile || !isChatPage || !desktopLayout) {
			wasLayoutOverflowingRef.current = false;
			return;
		}

		const constraints = new Set<HTMLElement>();
		let frame = 0;
		const update = () => {
			cancelAnimationFrame(frame);
			frame = requestAnimationFrame(() => {
				const isOverflowing =
					hasHorizontalOverflow(desktopLayout) ||
					Array.from(constraints).some(
						(constraint) =>
							hasHorizontalOverflow(constraint) ||
							Array.from(
								constraint.querySelectorAll<HTMLElement>("[data-desktop-layout-overflow-boundary]")
							).some(hasHorizontalOverflow)
					);
				if (
					!isCollapsed &&
					shouldAutoCollapseSidebar(wasLayoutOverflowingRef.current, isOverflowing)
				) {
					setIsCollapsed(true);
				}
				wasLayoutOverflowingRef.current = isOverflowing;
			});
		};
		const observer = new ResizeObserver(update);
		const syncConstraints = () => {
			const current = new Set(
				desktopLayout.querySelectorAll<HTMLElement>("[data-desktop-layout-constraint]")
			);
			for (const constraint of constraints) {
				if (!current.has(constraint)) {
					observer.unobserve(constraint);
					constraints.delete(constraint);
				}
			}
			for (const constraint of current) {
				if (!constraints.has(constraint)) {
					constraints.add(constraint);
					observer.observe(constraint);
				}
			}
		};
		observer.observe(desktopLayout);
		for (const column of desktopLayout.children) {
			observer.observe(column);
		}
		syncConstraints();
		const mutationObserver = new MutationObserver(() => {
			syncConstraints();
			update();
		});
		mutationObserver.observe(desktopLayout, { childList: true, subtree: true });
		update();

		return () => {
			cancelAnimationFrame(frame);
			mutationObserver.disconnect();
			observer.disconnect();
		};
	}, [isCollapsed, isChatPage, isMobile, setIsCollapsed]);

	// Memoize context value to prevent unnecessary re-renders
	const sidebarContextValue = useMemo(
		() => ({ isCollapsed, setIsCollapsed, toggleCollapsed }),
		[isCollapsed, setIsCollapsed, toggleCollapsed]
	);
	const closeMobileDocuments = () => {
		if (mobileDocumentsWorkspaceFromState) documentsPanel?.onOpenChange(false);
	};

	// Mobile layout
	if (isMobile) {
		return (
			<SidebarProvider value={sidebarContextValue}>
				<TooltipProvider delayDuration={0}>
					<div className={cn("flex h-screen w-full flex-col bg-panel", className)}>
						<Header
							mobileMenuTrigger={<MobileSidebarTrigger onClick={() => setMobileMenuOpen(true)} />}
						/>

						<MobileSidebar
							isOpen={mobileMenuOpen}
							onOpenChange={setMobileMenuOpen}
							workspaces={workspaces}
							activeWorkspaceId={activeWorkspaceId}
							onWorkspaceSelect={(id) => {
								closeMobileDocuments();
								onWorkspaceSelect(id);
							}}
							onAddWorkspace={onAddWorkspace}
							isAtWorkspaceLimit={isAtWorkspaceLimit}
							maxWorkspacesPerUser={maxWorkspacesPerUser}
							workspace={workspace}
							navItems={navItems}
							onNavItemClick={(item) => {
								if (item.url !== "#documents") closeMobileDocuments();
								onNavItemClick?.(item);
							}}
							chats={chats}
							activeChatId={activeChatId}
							onNewChat={() => {
								closeMobileDocuments();
								onNewChat();
							}}
							onChatSelect={(chat) => {
								closeMobileDocuments();
								onChatSelect(chat);
							}}
							onChatPrefetch={onChatPrefetch}
							onChatRename={onChatRename}
							onChatDelete={onChatDelete}
							onChatArchive={onChatArchive}
							onChatsClick={
								onChatsClick
									? () => {
											closeMobileDocuments();
											onChatsClick();
										}
									: undefined
							}
							onViewAllChats={
								onViewAllChats
									? () => {
											closeMobileDocuments();
											onViewAllChats();
										}
									: undefined
							}
							isAllChatsActive={isAllChatsPage}
							user={user}
							onSettings={onSettings}
							onUserSettings={onUserSettings}
							notifications={notifications}
							onLogout={onLogout}
							pageUsage={pageUsage}
							theme={theme}
							setTheme={setTheme}
							isLoadingChats={isLoadingChats}
						/>

						{mobileDocumentsWorkspaceFromState && documentsPanel?.open ? (
							<WorkspacePanel
								viewportClassName="items-start justify-center overflow-hidden px-6 py-8"
								contentClassName="h-full max-w-5xl select-none"
							>
								<MobileDocumentsWorkspaceView onOpenChange={documentsPanel.onOpenChange} />
							</WorkspacePanel>
						) : useWorkspacePanel ? (
							<WorkspacePanel
								viewportClassName={workspacePanelViewportClassName}
								contentClassName={workspacePanelContentClassName}
							>
								{children}
							</WorkspacePanel>
						) : (
							<main className={cn("flex-1", isChatPage ? "overflow-hidden" : "overflow-auto")}>
								{children}
							</main>
						)}
						<MobileArtifactDrawer />
						<MobileDocumentViewerPanel />
					</div>
				</TooltipProvider>
			</SidebarProvider>
		);
	}

	// Desktop layout
	return (
		<SidebarProvider value={sidebarContextValue}>
			<TooltipProvider delayDuration={0}>
				<div className={cn("flex h-screen w-full flex-col overflow-hidden bg-rail", className)}>
					<div
						ref={desktopLayoutRef}
						data-desktop-layout
						data-sidebar-collapsed={isCollapsed}
						className="grid min-h-0 w-full flex-1 grid-cols-[auto_auto_minmax(0,1fr)] overflow-hidden pl-2"
					>
						<div className="hidden overflow-hidden border-r bg-rail md:flex">
							<IconRail
								workspaces={workspaces}
								activeWorkspaceId={activeWorkspaceId}
								onWorkspaceSelect={onWorkspaceSelect}
								onWorkspaceDelete={onWorkspaceDelete}
								onWorkspaceSettings={onWorkspaceSettings}
								onAddWorkspace={onAddWorkspace}
								isAtWorkspaceLimit={isAtWorkspaceLimit}
								maxWorkspacesPerUser={maxWorkspacesPerUser}
								isSingleRailMode={false}
								user={user}
								onUserSettings={onUserSettings}
								notifications={notifications}
								onLogout={onLogout}
								theme={theme}
								setTheme={setTheme}
							/>
						</div>

						{/* Sidebar slide-outs remain overlays; the shell columns themselves are intrinsic grid tracks. */}
						<div className="relative z-20 hidden shrink-0 border-r bg-panel md:flex">
							<Sidebar
								workspace={workspace}
								isCollapsed={isCollapsed}
								onToggleCollapse={toggleCollapsed}
								navItems={navItems}
								onNavItemClick={onNavItemClick}
								chats={chats}
								activeChatId={activeChatId}
								onNewChat={onNewChat}
								onChatSelect={onChatSelect}
								onChatPrefetch={onChatPrefetch}
								onChatRename={onChatRename}
								onChatDelete={onChatDelete}
								onChatArchive={onChatArchive}
								onChatsClick={onChatsClick}
								onViewAllChats={onViewAllChats}
								isAllChatsActive={isAllChatsPage}
								user={user}
								onSettings={onSettings}
								onUserSettings={onUserSettings}
								onLogout={onLogout}
								pageUsage={pageUsage}
								theme={theme}
								setTheme={setTheme}
								renderUserProfile={false}
								className="flex shrink-0"
								isLoadingChats={isLoadingChats}
								sidebarWidth={sidebarWidth}
								isResizing={isResizing}
							/>

							{!isCollapsed && (
								<hr
									aria-orientation="vertical"
									aria-label="Resize sidebar"
									aria-valuemin={SIDEBAR_MIN_WIDTH}
									aria-valuemax={SIDEBAR_MAX_WIDTH}
									aria-valuenow={sidebarWidth}
									tabIndex={0}
									onPointerDown={onResizePointerDown}
									style={{ touchAction: "none" }}
									className={cn(
										"absolute top-0 right-0 h-full w-4 translate-x-1/2 z-50 m-0 border-0 bg-transparent p-0 select-none cursor-col-resize",
										"after:content-[''] after:absolute after:inset-y-0 after:left-1/2 after:w-px after:-translate-x-1/2 after:bg-transparent hover:after:bg-border/80 after:transition-colors",
										isResizing && "after:bg-border"
									)}
								/>
							)}
						</div>

						{useWorkspacePanel ? (
							<WorkspacePanel
								viewportClassName={workspacePanelViewportClassName}
								contentClassName={workspacePanelContentClassName}
							>
								{children}
							</WorkspacePanel>
						) : (
							<WorkspaceSplit
								primary={
									<MainContentPanel
										isChatPage={isChatPage}
										onTabSwitch={onTabSwitch}
										onTabPrefetch={onTabPrefetch}
										onNewChat={onNewChat}
										showTabs={showTabs}
									>
										{children}
									</MainContentPanel>
								}
								secondary={<RightPanel layout={rightPanelLayout} documentsPanel={documentsPanel} />}
								secondaryTab={rightPanelLayout.effectiveTab}
								secondaryVisible={rightPanelLayout.isVisible}
								overlay={
									<div className="absolute right-2 top-2 z-30">
										<RightPanelToggleButton documentsOnly />
									</div>
								}
							/>
						)}
					</div>
					<MobileArtifactDrawer />
					<MobileDocumentViewerPanel />
				</div>
			</TooltipProvider>
		</SidebarProvider>
	);
}
