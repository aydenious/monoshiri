"use client";

import { useAtomValue, useSetAtom } from "jotai";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type React from "react";
import { useEffect } from "react";
import { llmSetupStatusAtomFamily } from "@/atoms/model-connections/model-connections-query.atoms";
import { activeWorkspaceIdAtom } from "@/atoms/workspaces/workspace-query.atoms";
import { DocumentUploadDialogProvider } from "@/components/assistant-ui/document-upload-popup";
import { RetrievalScopeProvider } from "@/components/assistant-ui/retrieval-scope-provider";
import { LayoutDataProvider } from "@/components/layout";
import { OnboardingTour } from "@/components/onboarding-tour";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { RetrievalScope } from "@/contracts/types/retrieval-scope.types";
import { useGlobalLoadingEffect } from "@/hooks/use-global-loading";

export function DashboardClientLayout({
	children,
	workspaceId,
	initialRetrievalScope,
	initialSidebarCollapsed,
	initialSidebarWidth,
	initialRightPanelCollapsed,
}: {
	children: React.ReactNode;
	workspaceId: string;
	initialRetrievalScope: RetrievalScope;
	initialSidebarCollapsed: boolean;
	initialSidebarWidth: number;
	initialRightPanelCollapsed: boolean;
}) {
	const t = useTranslations("dashboard");
	const router = useRouter();
	const pathname = usePathname();
	const { workspace_id } = useParams();
	const activeWorkspaceId = useAtomValue(activeWorkspaceIdAtom);
	const setActiveWorkspaceIdState = useSetAtom(activeWorkspaceIdAtom);

	// Single source of truth for the onboarding gate. Keyed by the route
	// workspaceId so the verdict can never lag behind a workspace switch.
	const {
		data: setupStatus,
		isLoading: setupLoading,
		error: setupError,
	} = useAtomValue(llmSetupStatusAtomFamily(Number(workspaceId)));

	const isOnboardingPage = pathname?.includes("/onboard");
	const isWorkspaceReady = activeWorkspaceId === workspaceId;

	const isReady = setupStatus?.status === "ready";

	// First-run (initial_setup) is the only not-ready state that redirects, so
	// recovery falls through to the inline composer notice and an established
	// user who lost their models is never re-onboarded. The other direction
	// leaves onboarding once the workspace can chat.
	useEffect(() => {
		if (setupLoading || setupError) return;
		if (setupStatus?.stage === "initial_setup" && !isOnboardingPage) {
			router.replace(`/dashboard/${workspaceId}/onboard`);
		} else if (isReady && isOnboardingPage) {
			router.replace(`/dashboard/${workspaceId}/new-chat`);
		}
	}, [
		setupLoading,
		setupError,
		setupStatus?.stage,
		isReady,
		isOnboardingPage,
		router,
		workspaceId,
	]);

	useEffect(() => {
		const htmlBackground = document.documentElement.style.backgroundColor;
		const bodyBackground = document.body.style.backgroundColor;

		document.documentElement.style.backgroundColor = "var(--panel)";
		document.body.style.backgroundColor = "var(--panel)";

		return () => {
			document.documentElement.style.backgroundColor = htmlBackground;
			document.body.style.backgroundColor = bodyBackground;
		};
	}, []);

	useEffect(() => {
		const activeSeacrhSpaceId =
			typeof workspace_id === "string"
				? workspace_id
				: Array.isArray(workspace_id) && workspace_id.length > 0
					? workspace_id[0]
					: "";
		if (!activeSeacrhSpaceId) return;
		setActiveWorkspaceIdState(activeSeacrhSpaceId);
	}, [workspace_id, setActiveWorkspaceIdState]);

	// Suppress children during either pending redirect so neither /new-chat nor
	// /onboard flashes for a frame.
	const isLeavingOnboarding = isReady && isOnboardingPage;
	const isEnteringOnboarding = setupStatus?.stage === "initial_setup" && !isOnboardingPage;
	const isRedirecting =
		!setupLoading && !setupError && (isLeavingOnboarding || isEnteringOnboarding);

	// Block children until the workspace is synced and the initial verdict is
	// in; afterwards the composer renders its own not-ready state in place, so
	// recovery (e.g. deleting the last model) never triggers a full-screen
	// loader or a redirect.
	const shouldShowLoading = !setupError && (!isWorkspaceReady || setupLoading || isRedirecting);

	useGlobalLoadingEffect(shouldShowLoading);

	if (shouldShowLoading) {
		return null;
	}

	if (setupError) {
		return (
			<div className="flex flex-col items-center justify-center min-h-screen space-y-4">
				<Card className="w-[400px] bg-background/60 backdrop-blur-sm border-destructive/20">
					<CardHeader className="pb-2">
						<CardTitle className="text-xl font-medium text-destructive">
							{t("config_error")}
						</CardTitle>
						<CardDescription>{t("failed_load_llm_config")}</CardDescription>
					</CardHeader>
					<CardContent>
						<p className="text-sm text-muted-foreground">
							{setupError instanceof Error ? setupError.message : String(setupError)}
						</p>
					</CardContent>
				</Card>
			</div>
		);
	}

	if (isOnboardingPage) {
		return <>{children}</>;
	}

	return (
		<RetrievalScopeProvider
			key={workspaceId}
			workspaceId={Number(workspaceId)}
			initialScope={initialRetrievalScope}
		>
			<DocumentUploadDialogProvider>
				<OnboardingTour />
				<LayoutDataProvider
					workspaceId={workspaceId}
					initialSidebarCollapsed={initialSidebarCollapsed}
					initialSidebarWidth={initialSidebarWidth}
					initialRightPanelCollapsed={initialRightPanelCollapsed}
				>
					{children}
				</LayoutDataProvider>
			</DocumentUploadDialogProvider>
		</RetrievalScopeProvider>
	);
}
