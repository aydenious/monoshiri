"use client";
import { TriangleAlert } from "lucide-react";
import { JsonView } from "@/components/json-view";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { IssuesAlert } from "../issues-alert";

interface JsonModePanelProps {
	value: Record<string, unknown>;
	issues: string[];
	notice?: string;
	onChange: (next: Record<string, unknown>) => void;
}

/**
 * Raw-JSON escape hatch. Edits the same payload the form produces; the
 * orchestrator validates it against the contract schema on submit. Shown when
 * the user opts into "Edit as JSON" or when an existing definition uses
 * features the form can't represent.
 */
export function JsonModePanel({ value, issues, notice, onChange }: JsonModePanelProps) {
	return (
		<div className="space-y-4">
			{notice && (
				<Alert variant="warning">
					<TriangleAlert aria-hidden />
					<AlertDescription>{notice}</AlertDescription>
				</Alert>
			)}

			<JsonView
				src={value}
				editable
				onChange={(next) => onChange(next as Record<string, unknown>)}
				collapsed={false}
				className="max-h-144 overflow-auto rounded-md border border-accent bg-accent/20"
			/>

			<IssuesAlert issues={issues} />
		</div>
	);
}
