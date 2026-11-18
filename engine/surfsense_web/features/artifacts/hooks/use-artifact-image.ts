"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import {
	artifactImageBlobQueryKey,
	artifactManifestQueryOptions,
} from "@/features/artifacts/api/artifact-queries";
import { selectPrimaryArtifactFile } from "@/features/artifacts/lib/artifact-selectors";
import { authenticatedFetch } from "@/lib/auth-fetch";
import { buildBackendUrl } from "@/lib/env-config";

export function useArtifactImage(workspaceId: number, artifactId: number) {
	const manifestQuery = useQuery(artifactManifestQueryOptions(workspaceId, artifactId));
	const primary = selectPrimaryArtifactFile(manifestQuery.data);

	const imageQuery = useQuery({
		queryKey: artifactImageBlobQueryKey(workspaceId, artifactId, null, primary?.file_id),
		enabled: primary != null,
		queryFn: async () => {
			const blob = await fetchPrimary(primary?.content_url);
			return URL.createObjectURL(blob);
		},
		staleTime: 60_000,
	});

	useEffect(() => {
		const url = imageQuery.data;
		return () => {
			if (url) URL.revokeObjectURL(url);
		};
	}, [imageQuery.data]);

	return {
		src: imageQuery.data,
		loading: manifestQuery.isLoading || imageQuery.isLoading,
		error: manifestQuery.error ?? imageQuery.error,
	};
}

async function fetchPrimary(contentUrl: string | undefined): Promise<Blob> {
	if (!contentUrl) throw new Error("missing primary file");
	const response = await authenticatedFetch(buildBackendUrl(contentUrl), {
		cache: "no-store",
	});
	if (!response.ok) throw new Error(`Failed to load image: ${response.status}`);
	return response.blob();
}
