import { z } from "zod";

export const adminUser = z.object({
	id: z.uuid(),
	email: z.string(),
	display_name: z.string().nullable(),
	is_active: z.boolean(),
	is_superuser: z.boolean(),
	created_at: z.string().nullable(),
	last_login_at: z.string().nullable(),
});

export const createInviteRequest = z.object({
	note: z.string().max(200).optional(),
	days: z.number().int().min(1).max(30).optional(),
});

export const createInviteResponse = z.object({
	url: z.string(),
	jti: z.string(),
	expires_at: z.string(),
});

export const pendingInvite = z.object({
	jti: z.string(),
	note: z.string(),
	created_by: z.string(),
	created_at: z.string(),
	expires_at: z.string(),
});

export const listAdminUsersResponse = z.array(adminUser);
export const listInvitesResponse = z.array(pendingInvite);
export const deleteUserResponse = z.object({ status: z.string() });

export type AdminUser = z.infer<typeof adminUser>;
export type CreateInviteRequest = z.infer<typeof createInviteRequest>;
export type CreatedInvite = z.infer<typeof createInviteResponse>;
export type PendingInvite = z.infer<typeof pendingInvite>;
