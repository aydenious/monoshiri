"use client";

import { useAtomValue } from "jotai";
import { usePathname } from "next/navigation";
import { currentThreadAtom } from "@/atoms/chat/current-thread.atom";
import { activeTabAtom } from "@/atoms/tabs/tabs.atom";
import { ActionLogButton } from "@/components/agent-action-log/action-log-button";
import { ArtifactsToggleButton } from "@/features/chat-artifacts";
import { useMediaQuery } from "@/hooks/use-media-query";

interface HeaderProps {
	mobileMenuTrigger?: React.ReactNode;
}

export function Header({ mobileMenuTrigger }: HeaderProps) {
	const pathname = usePathname();
	const activeTab = useAtomValue(activeTabAtom);
	const isDesktop = useMediaQuery("(min-width: 768px)");

	const isChatPage = pathname?.includes("/new-chat") ?? false;
	const isDocumentTab = activeTab?.type === "document";

	const currentThreadState = useAtomValue(currentThreadAtom);

	const hasThread = isChatPage && !isDocumentTab && currentThreadState.id !== null;

	return (
		<header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 bg-main-panel/95 backdrop-blur supports-backdrop-filter:bg-main-panel/60 px-4">
			{/* Left side - Mobile menu trigger */}
			<div className="flex flex-1 items-center gap-2 min-w-0">{mobileMenuTrigger}</div>

			{/* Right side - Actions */}
			<div className="ml-auto flex items-center gap-2">
				{hasThread && isDesktop ? <ActionLogButton threadId={currentThreadState.id} /> : null}
				{hasThread && <ArtifactsToggleButton />}
			</div>
		</header>
	);
}
