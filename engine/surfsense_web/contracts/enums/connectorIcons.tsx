import {
	Brain,
	File,
	FileText,
	Globe,
	Microscope,
	Sparkles,
	Telescope,
	Webhook,
} from "lucide-react";
import Image from "next/image";

/**
 * Icon for a document type (or retrieval-depth label).
 *
 * Third-party connectors were removed from the fork, but documents that came
 * from them still exist and list normally. Their types are not special-cased
 * any more: they fall through to the generic file icon.
 */
export const getConnectorIcon = (documentType: string, className?: string) => {
	const iconProps = { className: className || "h-4 w-4" };
	const imgProps = {
		className: `${className || "h-5 w-5"} select-none pointer-events-none`,
		width: 20,
		height: 20,
		draggable: false as const,
	};

	switch (documentType) {
		case "YOUTUBE_CONNECTOR":
		case "YOUTUBE_VIDEO":
			return <Image src="/connectors/youtube.svg" alt="YouTube" {...imgProps} />;
		case "CRAWLED_URL":
			return <Globe {...iconProps} />;
		case "NOTE":
			return <FileText {...iconProps} />;
		case "EXTENSION":
			return <Webhook {...iconProps} />;
		case "USER_MEMORY":
		case "TEAM_MEMORY":
			return <Brain {...iconProps} />;
		case "DEEP":
			return <Sparkles {...iconProps} />;
		case "DEEPER":
			return <Microscope {...iconProps} />;
		case "DEEPEST":
			return <Telescope {...iconProps} />;
		default:
			// FILE, LOCAL_FOLDER_FILE and every removed connector type.
			return <File {...iconProps} />;
	}
};
