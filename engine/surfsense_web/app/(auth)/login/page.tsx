"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";
import { Logo } from "@/components/Logo";
import { useRuntimeConfig } from "@/components/providers/runtime-config";
import { getAuthErrorDetails, shouldRetry } from "@/lib/auth-errors";
import { setRedirectPath } from "@/lib/auth-utils";
import { AuthErrorAlert } from "../components/AuthErrorAlert";
import { GoogleLoginButton } from "./GoogleLoginButton";
import { LocalLoginForm } from "./LocalLoginForm";

/**
 * Styled by `app/(auth)/auth.css` (palette and `ss-home-*` classes), applied
 * by `app/(auth)/layout.tsx`.
 */

function LoginContent() {
	const t = useTranslations("auth");
	const tCommon = useTranslations("common");
	const router = useRouter();
	const { authType } = useRuntimeConfig();
	const [urlError, setUrlError] = useState<{ title: string; message: string } | null>(null);
	const searchParams = useSearchParams();

	useEffect(() => {
		// Check for various URL parameters that might indicate success or error states
		const registered = searchParams.get("registered");
		const error = searchParams.get("error");
		const message = searchParams.get("message");
		const logout = searchParams.get("logout");
		const returnUrl = searchParams.get("returnUrl");

		// Save returnUrl for client-side login flows that can redirect directly after success.
		if (returnUrl) {
			setRedirectPath(decodeURIComponent(returnUrl));
		}

		// Show registration success message
		if (registered === "true") {
			toast.success(t("register_success"), {
				description: t("login_subtitle"),
				duration: 5000,
			});
		}

		// Show logout confirmation
		if (logout === "true") {
			toast.success(tCommon("success"), {
				description: "You have been securely logged out",
				duration: 3000,
			});
		}

		// Show error messages from OAuth or other flows using auth-errors utility
		if (error) {
			// Use the auth-errors utility to get proper error details
			const errorDetails = getAuthErrorDetails(error);

			// If we have a custom message from URL params, use it as description
			const errorDescription = message ? decodeURIComponent(message) : errorDetails.description;

			// Set persistent error display
			setUrlError({
				title: errorDetails.title,
				message: errorDescription,
			});

			// Show toast with conditional retry action
			const toastOptions: {
				description: string;
				duration: number;
				action?: { label: string; onClick: () => void };
			} = {
				description: errorDescription,
				duration: 6000,
			};

			// Add retry action if the error is retryable
			if (shouldRetry(error)) {
				toastOptions.action = {
					label: "Retry",
					onClick: () => router.refresh(),
				};
			}

			toast.error(errorDetails.title, toastOptions);
		}

		// Show general messages
		if (message && !error && !registered && !logout) {
			toast.info("Notice", {
				description: decodeURIComponent(message),
				duration: 4000,
			});
		}
	}, [router, searchParams, t, tCommon]);

	if (authType === "GOOGLE") {
		return <GoogleLoginButton />;
	}

	return (
		<section className="ss-home-hero ss-home-pad flex min-h-screen items-center justify-center">
			<div className="mx-auto flex w-full max-w-md flex-col items-center text-center">
				<Logo priority className="h-14 w-14 rounded-md transition-all md:h-16 md:w-16" />
				<h1 className="ss-home-h2 mt-6 mb-2">{t("sign_in")}</h1>
				<p className="ss-home-body">{t("login_subtitle")}</p>

				{/* URL Error Display */}
				<AuthErrorAlert
					error={urlError}
					onDismiss={() => setUrlError(null)}
					className="mt-6 w-full rounded-md border border-destructive/30 bg-destructive/10 p-4 text-left text-destructive shadow-sm"
					iconClassName="shrink-0 mt-0.5 text-destructive"
					messageClassName="text-sm text-destructive/90"
					dismissClassName="size-6 shrink-0 text-destructive hover:bg-transparent hover:text-destructive/80"
				/>

				<div className="mt-8 w-full text-left">
					<LocalLoginForm />
				</div>
			</div>
		</section>
	);
}

export default function LoginPage() {
	// Suspense fallback returns null - the GlobalLoadingProvider handles the loading UI
	return (
		<Suspense fallback={null}>
			<LoginContent />
		</Suspense>
	);
}
