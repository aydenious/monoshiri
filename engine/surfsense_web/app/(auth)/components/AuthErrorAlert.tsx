"use client";

import { AnimatePresence, motion } from "motion/react";
import { Button } from "@/components/ui/button";

export type AuthError = {
	title: string | null;
	message: string | null;
};

/**
 * The animated error box shared by the auth screens. The three call sites carry
 * different colours and spacing, so each passes its own classes; the markup,
 * motion values and labels live here once.
 */
export function AuthErrorAlert({
	error,
	onDismiss,
	className,
	iconClassName,
	messageClassName,
	dismissClassName,
}: {
	error: AuthError | null;
	onDismiss: () => void;
	className: string;
	iconClassName: string;
	messageClassName: string;
	dismissClassName: string;
}) {
	return (
		<AnimatePresence>
			{error?.title && (
				<motion.div
					initial={{ opacity: 0, y: -10, scale: 0.95 }}
					animate={{ opacity: 1, y: 0, scale: 1 }}
					exit={{ opacity: 0, y: -10, scale: 0.95 }}
					transition={{ duration: 0.3 }}
					className={className}
				>
					<div className="flex items-start gap-3">
						<svg
							xmlns="http://www.w3.org/2000/svg"
							width="18"
							height="18"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							strokeWidth="2"
							strokeLinecap="round"
							strokeLinejoin="round"
							className={iconClassName}
						>
							<title>Error Icon</title>
							<circle cx="12" cy="12" r="10" />
							<line x1="15" y1="9" x2="9" y2="15" />
							<line x1="9" y1="9" x2="15" y2="15" />
						</svg>
						<div className="flex-1 min-w-0">
							<p className="text-sm font-semibold mb-1">{error.title}</p>
							<p className={messageClassName}>{error.message}</p>
						</div>
						<Button
							variant="ghost"
							size="icon"
							onClick={onDismiss}
							className={dismissClassName}
							aria-label="Dismiss error"
							type="button"
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								width="16"
								height="16"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								strokeWidth="2"
								strokeLinecap="round"
								strokeLinejoin="round"
							>
								<title>Close</title>
								<line x1="18" y1="6" x2="6" y2="18" />
								<line x1="6" y1="6" x2="18" y2="18" />
							</svg>
						</Button>
					</div>
				</motion.div>
			)}
		</AnimatePresence>
	);
}
