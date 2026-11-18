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

interface ModelSelectorProps {
	workspaceId: number;
	className?: string;
	onChatModelSelected?: () => void;
}

export function ModelSelector({ workspaceId, className, onChatModelSelected }: ModelSelectorProps) {
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
	} = useModelSelector(workspaceId, "supports_chat");

	const chatModelsById = useMemo(
		() => new Map(allModels.map((model) => [model.id, model])),
		[allModels]
	);
	const selectedModelId = roles?.chat_model_id ?? AUTO_MODEL_ID;
	const selected = chatModelsById.get(selectedModelId);
	const showIconOnlyTrigger = isMobile;

	function selectModel(modelId: number) {
		updateRoles.mutate({ chat_model_id: modelId });
		closeSelector();
		requestAnimationFrame(() => {
			onChatModelSelected?.();
		});
	}

	return (
		<ModelSelectorShell
			drawerTitle="Select Chat Model"
			isMobile={isMobile}
			onOpenChange={handleOpenChange}
			open={open}
			trigger={
				<ModelSelectorTrigger
					ariaLabel="Select chat model"
					className={className}
					selected={selected}
					showIconOnly={showIconOnlyTrigger}
				/>
			}
		>
			<ModelSelectorContent
				autoSelected={selectedModelId === AUTO_MODEL_ID}
				emptyText="No enabled chat models. Add or enable models in Settings."
				groups={groups}
				hasSearchQuery={hasSearchQuery}
				loading={loading}
				onManageModels={manageModelConnections}
				onScroll={handleScroll}
				onSearchChange={setSearch}
				onSelect={selectModel}
				scrollPos={scrollPos}
				search={search}
				searchEmptyText="No matching chat models."
				searchPlaceholder="Search chat models"
				selectedModelId={roles?.chat_model_id}
			/>
		</ModelSelectorShell>
	);
}
