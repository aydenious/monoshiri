import type { z } from "zod";
import {
	type ConnectionCreateRequest,
	type ConnectionRead,
	type ConnectionUpdateRequest,
	connectionCreateRequest,
	connectionListResponse,
	connectionRead,
	connectionUpdateRequest,
	type GlobalLlmConfigStatus,
	globalLlmConfigStatus,
	type LlmSetupStatus,
	llmSetupStatus,
	type ModelPreviewRead,
	type ModelProviderRead,
	type ModelRead,
	type ModelRoles,
	type ModelsBulkUpdateRequest,
	type ModelTestPreviewRequest,
	type ModelUpdateRequest,
	modelListResponse,
	modelPreviewListResponse,
	modelProviderListResponse,
	modelRead,
	modelRoles,
	modelsBulkUpdateRequest,
	modelTestPreviewRequest,
	modelUpdateRequest,
	type VerifyConnectionResponse,
	verifyConnectionResponse,
} from "@/contracts/types/model-connections.types";
import { ValidationError } from "../error";
import { baseApiService } from "./base-api.service";

function parseOrThrow<S extends z.ZodType>(schema: S, request: unknown): z.output<S> {
	const parsed = schema.safeParse(request);
	if (!parsed.success) {
		throw new ValidationError(parsed.error.issues.map((issue) => issue.message).join(", "));
	}
	return parsed.data;
}

/** Parse a request whose workspace-scoped form must carry a positive workspace_id. */
function parseScoped<S extends z.ZodType<{ scope?: string; workspace_id?: number | null }>>(
	schema: S,
	request: unknown
): z.output<S> {
	const parsed = parseOrThrow(schema, request);
	if (
		parsed.scope === "SEARCH_SPACE" &&
		(!Number.isFinite(parsed.workspace_id) || (parsed.workspace_id ?? 0) <= 0)
	) {
		throw new ValidationError("workspace_id is required");
	}
	return parsed;
}

class ModelConnectionsApiService {
	getGlobalConnections = async (): Promise<ConnectionRead[]> => {
		return baseApiService.get(`/api/v1/global-model-connections`, connectionListResponse);
	};

	getGlobalLlmConfigStatus = async (): Promise<GlobalLlmConfigStatus> => {
		return baseApiService.get(`/api/v1/global-llm-config-status`, globalLlmConfigStatus);
	};

	getLlmSetupStatus = async (workspaceId: number): Promise<LlmSetupStatus> => {
		return baseApiService.get(`/api/v1/workspaces/${workspaceId}/llm-setup-status`, llmSetupStatus);
	};

	getModelProviders = async (): Promise<ModelProviderRead[]> => {
		return baseApiService.get(`/api/v1/model-providers`, modelProviderListResponse);
	};

	getConnections = async (workspaceId: number): Promise<ConnectionRead[]> => {
		return baseApiService.get(
			`/api/v1/model-connections?workspace_id=${workspaceId}`,
			connectionListResponse
		);
	};

	createConnection = async (request: ConnectionCreateRequest): Promise<ConnectionRead> => {
		const parsed = parseScoped(connectionCreateRequest, request);
		return baseApiService.post(`/api/v1/model-connections`, connectionRead, { body: parsed });
	};

	updateConnection = async (
		id: number,
		request: ConnectionUpdateRequest
	): Promise<ConnectionRead> => {
		const parsed = parseOrThrow(connectionUpdateRequest, request);
		return baseApiService.put(`/api/v1/model-connections/${id}`, connectionRead, { body: parsed });
	};

	deleteConnection = async (id: number) => {
		return baseApiService.delete(`/api/v1/model-connections/${id}`, undefined);
	};

	verifyConnection = async (id: number): Promise<VerifyConnectionResponse> => {
		return baseApiService.post(`/api/v1/model-connections/${id}/verify`, verifyConnectionResponse);
	};

	discoverModels = async (id: number): Promise<ModelRead[]> => {
		return baseApiService.post(`/api/v1/model-connections/${id}/discover`, modelListResponse);
	};

	previewModels = async (request: ConnectionCreateRequest): Promise<ModelPreviewRead[]> => {
		const parsed = parseScoped(connectionCreateRequest, request);
		return baseApiService.post(
			`/api/v1/model-connections/discover-preview`,
			modelPreviewListResponse,
			{ body: parsed }
		);
	};

	testPreviewModel = async (
		request: ModelTestPreviewRequest
	): Promise<VerifyConnectionResponse> => {
		const parsed = parseScoped(modelTestPreviewRequest, request);
		return baseApiService.post(`/api/v1/model-connections/test-preview`, verifyConnectionResponse, {
			body: parsed,
		});
	};

	updateModel = async (id: number, request: ModelUpdateRequest): Promise<ModelRead> => {
		const parsed = parseOrThrow(modelUpdateRequest, request);
		return baseApiService.put(`/api/v1/models/${id}`, modelRead, { body: parsed });
	};

	bulkUpdateModels = async (
		connectionId: number,
		request: ModelsBulkUpdateRequest
	): Promise<ModelRead[]> => {
		const parsed = parseOrThrow(modelsBulkUpdateRequest, request);
		return baseApiService.request(
			`/api/v1/model-connections/${connectionId}/models`,
			modelListResponse,
			{
				method: "PATCH",
				headers: { "Content-Type": "application/json" },
				body: parsed,
			}
		);
	};

	testModel = async (id: number): Promise<VerifyConnectionResponse> => {
		return baseApiService.post(`/api/v1/models/${id}/test`, verifyConnectionResponse);
	};

	getModelRoles = async (workspaceId: number): Promise<ModelRoles> => {
		return baseApiService.get(`/api/v1/workspaces/${workspaceId}/model-roles`, modelRoles);
	};

	updateModelRoles = async (workspaceId: number, roles: ModelRoles): Promise<ModelRoles> => {
		return baseApiService.put(`/api/v1/workspaces/${workspaceId}/model-roles`, modelRoles, {
			body: roles,
		});
	};
}

export const modelConnectionsApiService = new ModelConnectionsApiService();
