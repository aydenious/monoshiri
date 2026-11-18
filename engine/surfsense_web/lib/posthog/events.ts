import type { ChatErrorKind, ChatErrorSeverity, ChatFlow } from "@/lib/chat/chat-error-classifier";

/**
 * Analytics event shims (Monoshiri: telemetry removed)
 *
 * Every tracker below keeps its name and signature so call sites are
 * unchanged, but nothing is sent anywhere.
 *
 * SCOPE: this file now holds only *intent* and client-perceived *UX* events.
 * Authoritative *outcome* events (resource creation, task/ingestion/indexing
 * completion, auth success, billing) are emitted server-side in
 * surfsense_backend/app/observability/analytics.py — they are reliable
 * regardless of ad-blockers, tab-close, or non-browser (MCP/PAT/OAuth)
 * clients. Do NOT re-add optimistic outcome captures here; they double-count.
 *
 * Events follow a consistent naming convention: category_action
 */

function safeCapture(_event: string, _properties?: Record<string, unknown>) {
	// Intentionally empty: no telemetry.
}

/**
 * Drop undefined values.
 */
function compact<T extends object>(obj: T): Record<string, unknown> {
	const out: Record<string, unknown> = {};
	for (const [k, v] of Object.entries(obj)) {
		if (v !== undefined) out[k] = v;
	}
	return out;
}

// ============================================
// AUTH EVENTS (attempts + failures only; successes are server-side)
// ============================================

export function trackLoginAttempt(method: "local" | "google") {
	safeCapture("auth_login_attempt", { method });
}

export function trackLoginFailure(method: "local" | "google", error?: string) {
	safeCapture("auth_login_failure", { method, error });
}

export function trackRegistrationAttempt() {
	safeCapture("auth_registration_attempt");
}

export function trackRegistrationFailure(error?: string) {
	safeCapture("auth_registration_failure", { error });
}

export function trackLogout() {
	safeCapture("auth_logout");
}

// ============================================
// CHAT EVENTS (client-perceived UX)
// ============================================

export function trackChatMessageSent(
	workspaceId: number,
	chatId: number,
	options?: {
		hasAttachments?: boolean;
		hasMentionedDocuments?: boolean;
		messageLength?: number;
	}
) {
	safeCapture("chat_message_sent", {
		workspace_id: workspaceId,
		chat_id: chatId,
		has_attachments: options?.hasAttachments ?? false,
		has_mentioned_documents: options?.hasMentionedDocuments ?? false,
		message_length: options?.messageLength,
	});
}

export function trackChatResponseReceived(workspaceId: number, chatId: number) {
	safeCapture("chat_response_received", {
		workspace_id: workspaceId,
		chat_id: chatId,
	});
}

export function trackChatError(workspaceId: number, chatId: number, error?: string) {
	safeCapture("chat_error", {
		workspace_id: workspaceId,
		chat_id: chatId,
		error,
	});
}

export function trackActivityTraceInteraction(
	action: "segment_expanded" | "segment_collapsed",
	properties?: {
		activityCount?: number;
		reasoningCount?: number;
		durationMs?: number;
		surface?: "desktop_inline" | "mobile_drawer";
	}
) {
	safeCapture(
		"chat_interleaved_activity_trace_interaction",
		compact({
			action,
			activity_count: properties?.activityCount,
			reasoning_count: properties?.reasoningCount,
			duration_ms: properties?.durationMs,
			surface: properties?.surface,
		})
	);
}

export function trackActivityTimingContractViolation(messageId: string, activeDurationMs: number) {
	safeCapture("chat_activity_timing_contract_violation", {
		kind: "missing_terminal_snapshot",
		message_id: messageId,
		active_duration_ms: activeDurationMs,
	});
}

export interface ChatFailureTelemetry {
	flow: ChatFlow;
	kind: ChatErrorKind;
	error_code?: string;
	severity: ChatErrorSeverity;
	is_expected: boolean;
	message?: string;
}

export function trackChatBlocked(
	workspaceId: number,
	chatId: number | null,
	payload: ChatFailureTelemetry
) {
	safeCapture(
		"chat_blocked",
		compact({
			workspace_id: workspaceId,
			chat_id: chatId ?? undefined,
			flow: payload.flow,
			kind: payload.kind,
			error_code: payload.error_code,
			severity: payload.severity,
			is_expected: payload.is_expected,
			message: payload.message,
		})
	);
}

export function trackChatErrorDetailed(
	workspaceId: number,
	chatId: number | null,
	payload: ChatFailureTelemetry
) {
	safeCapture(
		"chat_error",
		compact({
			workspace_id: workspaceId,
			chat_id: chatId ?? undefined,
			flow: payload.flow,
			kind: payload.kind,
			error_code: payload.error_code,
			severity: payload.severity,
			is_expected: payload.is_expected,
			message: payload.message,
		})
	);
}

// ============================================
// DOCUMENT EVENTS (intent only; ingestion outcome is server-side)
// ============================================

export function trackDocumentUploadStarted(
	workspaceId: number,
	fileCount: number,
	totalSizeBytes: number
) {
	safeCapture("document_upload_started", {
		workspace_id: workspaceId,
		file_count: fileCount,
		total_size_bytes: totalSizeBytes,
	});
}

// ============================================
// FEATURE USAGE EVENTS
// ============================================

export function trackDesktopDownloadClicked(options: {
	os: string;
	placement: "sidebar_collapsed" | "sidebar_expanded";
}) {
	safeCapture("desktop_download_clicked", {
		os: options.os,
		placement: options.placement,
	});
}

// ============================================
// MARKETING / REFERRAL EVENTS
// ============================================

export function trackReferralLanding(refCode: string, landingUrl: string) {
	safeCapture("marketing_referral_landing", {
		ref_code: refCode,
		landing_url: landingUrl,
		$set_once: { first_ref_code: refCode },
		$set: { latest_ref_code: refCode },
	});
}

// ============================================
// AUTOMATION EVENTS (failures + chat-builder UX; CRUD outcomes are server-side)
// ============================================

export function trackAutomationCreateFailed(props: { workspace_id?: number; error?: string }) {
	safeCapture("automation_create_failed", compact(props));
}

export function trackAutomationUpdateFailed(props: { automation_id: number; error?: string }) {
	safeCapture("automation_update_failed", compact(props));
}

export function trackAutomationDeleteFailed(props: { automation_id: number; error?: string }) {
	safeCapture("automation_delete_failed", compact(props));
}

export function trackAutomationTriggerAddFailed(props: { automation_id: number; error?: string }) {
	safeCapture("automation_trigger_add_failed", compact(props));
}

export function trackAutomationTriggerUpdateFailed(props: {
	automation_id: number;
	trigger_id: number;
	error?: string;
}) {
	safeCapture("automation_trigger_update_failed", compact(props));
}

export function trackAutomationTriggerRemoveFailed(props: {
	automation_id: number;
	trigger_id: number;
	error?: string;
}) {
	safeCapture("automation_trigger_remove_failed", compact(props));
}

interface AutomationChatDecisionProps {
	workspace_id?: number;
	edited?: boolean;
	task_count?: number;
	trigger_type?: string;
	chat_model_id?: number;
	image_gen_model_id?: number;
	vision_model_id?: number;
}

export function trackAutomationChatApproved(props: AutomationChatDecisionProps) {
	safeCapture("automation_chat_approved", compact(props));
}

export function trackAutomationChatRejected(props: { workspace_id?: number }) {
	safeCapture("automation_chat_rejected", compact(props));
}

export function trackAutomationChatDraftEdited(props: { workspace_id?: number }) {
	safeCapture("automation_chat_draft_edited", compact(props));
}

export function trackAutomationChatCreateSucceeded(props: {
	automation_id: number;
	name?: string;
	workspace_id?: number;
}) {
	safeCapture("automation_chat_create_succeeded", compact(props));
}

export function trackAutomationChatCreateFailed(props: {
	reason: "invalid" | "error";
	workspace_id?: number;
	issue_count?: number;
	message?: string;
}) {
	safeCapture("automation_chat_create_failed", compact(props));
}

// ============================================
// USER IDENTIFICATION
// ============================================

/** No-op: user identification is disabled. */
export function identifyUser(_userId: string, _properties?: Record<string, unknown>) {}

/** No-op: nothing to reset. */
export function resetUser() {}
