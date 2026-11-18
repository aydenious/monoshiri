"use client";

import { useAtom, useAtomValue } from "jotai";
import { useMemo } from "react";
import { updateModelRolesMutationAtom } from "@/atoms/model-connections/model-connections-mutation.atoms";
import { modelRolesAtom } from "@/atoms/model-connections/model-connections-query.atoms";
import {
	AUTO_MODEL_ID,
	ModelSelectorContent,
	ModelSelectorShell,
	ModelSelectorTrigger,
	useModelSelector,
} from "./model-selector-parts";

interface ImageModelSelectorProps {
	workspaceId: number;
	className?: string;
	mobileIconOnly?: boolean;
}

export function ImageModelSelector({
	workspaceId,
	className,
	mobileIconOnly = false,
}: ImageModelSelectorProps) {
	const [{ data: roles }] = useAtom(modelRolesAtom);
	const updateRoles = useAtomValue(updateModelRolesMutationAtom);
	const {
		allModels,
		closeSelector,
		groups,
		handleOpenChange,
		handleScroll,
		hasSearchQuery,
		isMobile,
		loading,
		manageModelConnections,
		open,
		scrollPos,
		search,
		setSearch,
	} = useModelSelector(workspaceId, "supports_image_generation");

	const imageModelsById = useMemo(
		() => new Map(allModels.map((model) => [model.id, model])),
		[allModels]
	);
	const selectedModelId = roles?.image_gen_model_id ?? AUTO_MODEL_ID;
	const selected = imageModelsById.get(selectedModelId);
	const showIconOnlyTrigger = isMobile && mobileIconOnly;

	function selectModel(modelId: number) {
		updateRoles.mutate({ image_gen_model_id: modelId });
		closeSelector();
	}

	// Only surface this control when usable image-generation models exist.
	if (!loading && allModels.length === 0) {
		return null;
	}

	return (
		<ModelSelectorShell
			drawerTitle="Select Image Model"
			isMobile={isMobile}
			onOpenChange={handleOpenChange}
			open={open}
			trigger={
				<ModelSelectorTrigger
					ariaLabel="Select image model"
					className={className}
					selected={selected}
					showIconOnly={showIconOnlyTrigger}
				/>
			}
		>
			<ModelSelectorContent
				autoSelected={selectedModelId === AUTO_MODEL_ID}
				emptyText="No enabled image models. Add or enable models in Settings."
				groups={groups}
				hasSearchQuery={hasSearchQuery}
				loading={loading}
				onManageModels={manageModelConnections}
				onScroll={handleScroll}
				onSearchChange={setSearch}
				onSelect={selectModel}
				scrollPos={scrollPos}
				search={search}
				searchEmptyText="No matching image models."
				searchPlaceholder="Search image models"
				selectedModelId={roles?.image_gen_model_id}
			/>
		</ModelSelectorShell>
	);
}
