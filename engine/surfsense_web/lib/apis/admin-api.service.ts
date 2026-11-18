import {
	adminUser,
	type CreateInviteRequest,
	createInviteRequest,
	createInviteResponse,
	deleteUserResponse,
	listAdminUsersResponse,
	listInvitesResponse,
} from "@/contracts/types/admin.types";
import { ValidationError } from "../error";
import { baseApiService } from "./base-api.service";

class AdminApiService {
	listUsers = async () => {
		return baseApiService.get("/api/v1/admin/users", listAdminUsersResponse);
	};

	createInvite = async (request: CreateInviteRequest) => {
		const parsed = createInviteRequest.safeParse(request);
		if (!parsed.success) {
			throw new ValidationError(
				`Invalid request: ${parsed.error.issues.map((issue) => issue.message).join(", ")}`
			);
		}
		return baseApiService.post("/api/v1/admin/invites", createInviteResponse, {
			body: parsed.data,
		});
	};

	listInvites = async () => {
		return baseApiService.get("/api/v1/admin/invites", listInvitesResponse);
	};

	revokeInvite = async (jti: string) => {
		return baseApiService.delete(`/api/v1/admin/invites/${encodeURIComponent(jti)}`);
	};

	deactivateUser = async (id: string) => {
		return baseApiService.post(`/api/v1/admin/users/${id}/deactivate`, adminUser);
	};

	activateUser = async (id: string) => {
		return baseApiService.post(`/api/v1/admin/users/${id}/activate`, adminUser);
	};

	deleteUser = async (id: string, confirmEmail: string) => {
		return baseApiService.delete(`/api/v1/admin/users/${id}`, deleteUserResponse, {
			body: { confirm_email: confirmEmail },
		});
	};
}

export const adminApiService = new AdminApiService();
