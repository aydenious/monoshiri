import { AlertCircle } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

export function IssuesAlert({ issues }: { issues: string[] }) {
	if (issues.length === 0) return null;
	return (
		<Alert variant="destructive">
			<AlertCircle aria-hidden />
			<AlertTitle>{issues.length === 1 ? "1 issue" : `${issues.length} issues`}</AlertTitle>
			<AlertDescription>
				<ul className="list-inside list-disc">
					{issues.map((issue) => (
						<li key={issue}>{issue}</li>
					))}
				</ul>
			</AlertDescription>
		</Alert>
	);
}
