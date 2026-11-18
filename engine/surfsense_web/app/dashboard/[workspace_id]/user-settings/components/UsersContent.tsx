"use client";

import { useAtomValue } from "jotai";
import { Check, Copy, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { currentUserAtom } from "@/atoms/user/user-query.atoms";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import type { AdminUser, CreatedInvite, PendingInvite } from "@/contracts/types/admin.types";
import { adminApiService } from "@/lib/apis/admin-api.service";
import { AppError } from "@/lib/error";
import { copyToClipboard } from "@/lib/utils";

export function UsersContent() {
	const t = useTranslations("userSettings");
	const { data: currentUser } = useAtomValue(currentUserAtom);
	const isAdmin = currentUser?.is_superuser === true;
	const [users, setUsers] = useState<AdminUser[]>([]);
	const [invites, setInvites] = useState<PendingInvite[]>([]);
	const [isLoading, setIsLoading] = useState(true);
	const [busy, setBusy] = useState(false);

	const [note, setNote] = useState("");
	const [days, setDays] = useState("7");
	const [created, setCreated] = useState<CreatedInvite | null>(null);
	const [copied, setCopied] = useState(false);

	const [deleteTarget, setDeleteTarget] = useState<AdminUser | null>(null);
	const [deleteEmail, setDeleteEmail] = useState("");

	const refresh = useCallback(async () => {
		try {
			const [u, i] = await Promise.all([
				adminApiService.listUsers(),
				adminApiService.listInvites(),
			]);
			setUsers(u);
			setInvites(i);
		} catch {
			toast.error(t("users_load_error"));
		} finally {
			setIsLoading(false);
		}
	}, [t]);

	useEffect(() => {
		if (isAdmin) void refresh();
	}, [isAdmin, refresh]);

	// Conflicts (e.g. "Cannot modify another admin here") carry a user-facing detail.
	const failToast = (error: unknown, fallbackKey: string) => {
		toast.error(error instanceof AppError && error.status === 409 ? error.message : t(fallbackKey));
	};

	const run = async (action: () => Promise<unknown>, errorKey: string) => {
		setBusy(true);
		try {
			await action();
			await refresh();
			return true;
		} catch (error) {
			failToast(error, errorKey);
			return false;
		} finally {
			setBusy(false);
		}
	};

	const handleCreateInvite = async () => {
		setBusy(true);
		try {
			const invite = await adminApiService.createInvite({
				note: note.trim() || undefined,
				days: Number(days) || undefined,
			});
			setCreated(invite);
			setNote("");
			await refresh();
		} catch {
			toast.error(t("users_invite_error"));
		} finally {
			setBusy(false);
		}
	};

	const handleCopy = async () => {
		if (!created) return;
		if (await copyToClipboard(created.url)) {
			setCopied(true);
			setTimeout(() => setCopied(false), 2000);
		}
	};

	const handleDelete = async () => {
		if (!deleteTarget) return;
		const ok = await run(
			() => adminApiService.deleteUser(deleteTarget.id, deleteEmail),
			"users_delete_error"
		);
		if (ok) {
			toast.success(t("users_delete_queued"));
			setDeleteTarget(null);
		}
	};

	if (currentUser && !isAdmin) {
		return (
			<p className="py-6 text-center text-sm text-muted-foreground">{t("users_admins_only")}</p>
		);
	}

	return (
		<div className="space-y-8 min-w-0">
			<section className="space-y-3">
				<div>
					<h3 className="text-sm font-semibold tracking-tight">{t("users_invite_title")}</h3>
					<p className="text-xs text-muted-foreground">{t("users_invite_desc")}</p>
				</div>
				<div className="grid gap-3 sm:grid-cols-[1fr_8rem_auto] sm:items-end">
					<div className="space-y-2">
						<Label htmlFor="invite-note">{t("users_invite_note_label")}</Label>
						<Input
							id="invite-note"
							value={note}
							maxLength={200}
							onChange={(e) => setNote(e.target.value)}
							placeholder={t("users_invite_note_placeholder")}
						/>
					</div>
					<div className="space-y-2">
						<Label htmlFor="invite-days">{t("users_invite_days_label")}</Label>
						<Input
							id="invite-days"
							type="number"
							min={1}
							max={30}
							value={days}
							onChange={(e) => setDays(e.target.value)}
						/>
					</div>
					<Button size="sm" className="h-9" disabled={busy} onClick={handleCreateInvite}>
						{t("users_invite_create")}
					</Button>
				</div>
			</section>

			<section className="space-y-3">
				<h3 className="text-sm font-semibold tracking-tight">{t("users_pending_title")}</h3>
				{invites.length === 0 ? (
					<p className="py-2 text-sm text-muted-foreground">{t("users_pending_empty")}</p>
				) : (
					<div className="grid grid-cols-1 gap-2">
						{invites.map((invite) => (
							<Card key={invite.jti} className="border-accent bg-accent/20">
								<CardContent className="flex items-center gap-3 p-3">
									<div className="min-w-0 flex-1">
										<p className="truncate text-sm font-medium">
											{invite.note || invite.jti.slice(0, 8)}
										</p>
										<p className="text-xs text-muted-foreground">
											{t("users_pending_meta", {
												date: new Date(invite.expires_at).toLocaleString(),
											})}
										</p>
									</div>
									<Button
										variant="ghost"
										size="sm"
										disabled={busy}
										onClick={() =>
											void run(
												() => adminApiService.revokeInvite(invite.jti),
												"users_invite_revoke_error"
											)
										}
									>
										{t("users_invite_revoke")}
									</Button>
								</CardContent>
							</Card>
						))}
					</div>
				)}
			</section>

			<section className="space-y-3">
				<h3 className="text-sm font-semibold tracking-tight">{t("users_list_title")}</h3>
				{isLoading ? (
					<Skeleton className="h-16 w-full bg-accent" />
				) : users.length === 0 ? (
					<p className="py-2 text-sm text-muted-foreground">{t("users_list_empty")}</p>
				) : (
					<div className="grid grid-cols-1 gap-2">
						{users.map((user) => (
							<Card key={user.id} className="border-accent bg-accent/20">
								<CardContent className="flex flex-wrap items-center gap-3 p-3">
									<div className="min-w-0 flex-1">
										<div className="flex items-center gap-2">
											<p className="truncate text-sm font-medium">{user.email}</p>
											{user.is_superuser ? (
												<span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
													{t("users_badge_admin")}
												</span>
											) : null}
											{user.is_active ? null : (
												<span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-medium text-destructive">
													{t("users_badge_inactive")}
												</span>
											)}
										</div>
										<p className="text-xs text-muted-foreground">
											{user.last_login_at
												? t("users_last_login", {
														date: new Date(user.last_login_at).toLocaleString(),
													})
												: t("users_last_login_never")}
										</p>
									</div>
									{user.is_superuser ? null : (
										<div className="flex items-center gap-1">
											<Button
												variant="outline"
												size="sm"
												disabled={busy}
												onClick={() =>
													void run(
														() =>
															user.is_active
																? adminApiService.deactivateUser(user.id)
																: adminApiService.activateUser(user.id),
														"users_action_error"
													)
												}
											>
												{user.is_active ? t("users_deactivate") : t("users_activate")}
											</Button>
											<Button
												variant="ghost"
												size="icon"
												aria-label={t("users_delete")}
												disabled={busy}
												className="h-8 w-8 text-muted-foreground hover:text-destructive"
												onClick={() => {
													setDeleteEmail("");
													setDeleteTarget(user);
												}}
											>
												<Trash2 className="h-4 w-4" />
											</Button>
										</div>
									)}
								</CardContent>
							</Card>
						))}
					</div>
				)}
			</section>

			<Dialog open={!!created} onOpenChange={(open) => !open && setCreated(null)}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>{t("users_invite_created_title")}</DialogTitle>
						<DialogDescription>{t("users_invite_created_desc")}</DialogDescription>
					</DialogHeader>
					<div className="flex min-w-0 items-start gap-2 rounded-md border border-border/60 bg-muted/30 p-2">
						<code className="min-w-0 flex-1 select-all break-all text-xs">{created?.url}</code>
						<Button
							variant="outline"
							size="sm"
							onClick={handleCopy}
							className="shrink-0 border-0 bg-muted/30 hover:bg-muted/50"
						>
							{copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
						</Button>
					</div>
					<DialogFooter>
						<Button onClick={() => setCreated(null)}>{t("users_invite_done")}</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
				<DialogContent className="sm:max-w-lg">
					<DialogHeader>
						<DialogTitle>{t("users_delete_title")}</DialogTitle>
						<DialogDescription>
							{t("users_delete_desc", { email: deleteTarget?.email ?? "" })}
						</DialogDescription>
					</DialogHeader>
					<div className="space-y-2">
						<Label htmlFor="delete-user-email">
							{t("users_delete_confirm_label", { email: deleteTarget?.email ?? "" })}
						</Label>
						<Input
							id="delete-user-email"
							autoComplete="off"
							value={deleteEmail}
							onChange={(e) => setDeleteEmail(e.target.value)}
						/>
					</div>
					<DialogFooter>
						<Button variant="ghost" onClick={() => setDeleteTarget(null)} disabled={busy}>
							{t("users_delete_cancel")}
						</Button>
						<Button
							variant="destructive"
							onClick={handleDelete}
							disabled={
								busy || deleteEmail.trim().toLowerCase() !== deleteTarget?.email.toLowerCase()
							}
							className="relative"
						>
							<span className={busy ? "opacity-0" : ""}>{t("users_delete_confirm")}</span>
							{busy && <Spinner size="sm" className="absolute" />}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
