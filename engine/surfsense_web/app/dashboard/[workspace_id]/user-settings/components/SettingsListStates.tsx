import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const SKELETON_KEYS = ["skeleton-a", "skeleton-b", "skeleton-c"];

export function CardListSkeleton() {
	return (
		<div className="-m-1 space-y-2 p-1">
			{SKELETON_KEYS.map((key) => (
				<Card key={key} className="border-accent bg-accent/20">
					<CardContent className="p-4 flex flex-col gap-3 min-h-24">
						<Skeleton className="h-4 w-32 md:w-40 bg-accent" />
						<Skeleton className="h-3 w-full bg-accent" />
						<Skeleton className="h-3 w-24 md:w-28 bg-accent mt-auto" />
					</CardContent>
				</Card>
			))}
		</div>
	);
}

export function LoadErrorAlert({ title, children }: { title: string; children: ReactNode }) {
	return (
		<Alert variant="destructive">
			<AlertTriangle />
			<AlertTitle>{title}</AlertTitle>
			<AlertDescription>{children}</AlertDescription>
		</Alert>
	);
}
