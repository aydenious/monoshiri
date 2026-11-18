import { atom } from "jotai";
import { rightPanelCollapsedAtom, rightPanelTabAtom } from "@/atoms/layout/right-panel.atom";

interface EditorPanelState {
	isOpen: boolean;
	workspaceId: number | null;
	memoryScope: "user" | "team" | null;
	title: string | null;
}

const initialState: EditorPanelState = {
	isOpen: false,
	workspaceId: null,
	memoryScope: null,
	title: null,
};

export const editorPanelAtom = atom<EditorPanelState>(initialState);

export const editorPanelOpenAtom = atom((get) => get(editorPanelAtom).isOpen);

const preEditorCollapsedAtom = atom<boolean | null>(null);

export const openEditorPanelAtom = atom(
	null,
	(
		get,
		set,
		payload: {
			memoryScope: "user" | "team";
			title?: string;
			workspaceId?: number;
		}
	) => {
		if (!get(editorPanelAtom).isOpen) {
			set(preEditorCollapsedAtom, get(rightPanelCollapsedAtom));
		}
		set(editorPanelAtom, {
			isOpen: true,
			workspaceId: payload.workspaceId ?? null,
			memoryScope: payload.memoryScope,
			title: payload.title ?? null,
		});
		set(rightPanelTabAtom, "editor");
		set(rightPanelCollapsedAtom, false);
	}
);

export const closeEditorPanelAtom = atom(null, (get, set) => {
	set(editorPanelAtom, initialState);
	set(rightPanelTabAtom, "sources");
	const prev = get(preEditorCollapsedAtom);
	if (prev !== null) {
		set(rightPanelCollapsedAtom, prev);
		set(preEditorCollapsedAtom, null);
	}
});
