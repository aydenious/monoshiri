import { z } from "zod";
import { role } from "./roles.types";

export const membership = z.object({
	id: z.number(),
	user_id: z.string(),
	workspace_id: z.number(),
	role_id: z.number().nullable(),
	is_owner: z.boolean(),
	joined_at: z.string(),
	created_at: z.string(),
	role: role.nullable().optional(),
	user_email: z.string().nullable().optional(),
	user_display_name: z.string().nullable().optional(),
	user_avatar_url: z.string().nullable().optional(),
	user_last_login: z.string().nullable().optional(),
	user_is_active: z.boolean().nullable().optional(),
});

/**
 * Get members
 */
export const getMembersRequest = z.object({
	workspace_id: z.number(),
});

export const getMembersResponse = z.array(membership);

/**
 * Get my access
 */
export const getMyAccessRequest = z.object({
	workspace_id: z.number(),
});

export const getMyAccessResponse = z.object({
	workspace_name: z.string(),
	workspace_id: z.number(),
	is_owner: z.boolean(),
	permissions: z.array(z.string()),
	role_name: z.string().nullable(),
});

export type Membership = z.infer<typeof membership>;
export type GetMembersRequest = z.infer<typeof getMembersRequest>;
export type GetMembersResponse = z.infer<typeof getMembersResponse>;
export type GetMyAccessRequest = z.infer<typeof getMyAccessRequest>;
export type GetMyAccessResponse = z.infer<typeof getMyAccessResponse>;
