import {
	type GetMembersRequest,
	type GetMyAccessRequest,
	getMembersRequest,
	getMembersResponse,
	getMyAccessRequest,
	getMyAccessResponse,
} from "@/contracts/types/members.types";
import { ValidationError } from "@/lib/error";
import { baseApiService } from "./base-api.service";

class MembersApiService {
	/**
	 * Get members of a workspace
	 */
	getMembers = async (request: GetMembersRequest) => {
		const parsedRequest = getMembersRequest.safeParse(request);

		if (!parsedRequest.success) {
			console.error("Invalid request:", parsedRequest.error);

			const errorMessage = parsedRequest.error.issues.map((issue) => issue.message).join(", ");
			throw new ValidationError(`Invalid request: ${errorMessage}`);
		}

		return baseApiService.get(
			`/api/v1/workspaces/${parsedRequest.data.workspace_id}/members`,
			getMembersResponse
		);
	};

	/**
	 * Get current user's access information for a workspace
	 */
	getMyAccess = async (request: GetMyAccessRequest) => {
		const parsedRequest = getMyAccessRequest.safeParse(request);

		if (!parsedRequest.success) {
			console.error("Invalid request:", parsedRequest.error);

			const errorMessage = parsedRequest.error.issues.map((issue) => issue.message).join(", ");
			throw new ValidationError(`Invalid request: ${errorMessage}`);
		}

		return baseApiService.get(
			`/api/v1/workspaces/${parsedRequest.data.workspace_id}/my-access`,
			getMyAccessResponse
		);
	};
}

export const membersApiService = new MembersApiService();
