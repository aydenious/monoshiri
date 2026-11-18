import { Cpu, Shuffle } from "lucide-react";
import type { ComponentType } from "react";
import {
	Ai21Icon,
	AnyscaleIcon,
	AzureIcon,
	BedrockIcon,
	CerebrasIcon,
	ClaudeIcon,
	CloudflareIcon,
	CohereIcon,
	CometApiIcon,
	DatabricksIcon,
	DeepInfraIcon,
	DeepSeekIcon,
	FireworksAiIcon,
	GeminiIcon,
	GitHubModelsIcon,
	GroqIcon,
	HuggingFaceIcon,
	LmStudioIcon,
	MiniMaxIcon,
	MistralIcon,
	MoonshotIcon,
	NscaleIcon,
	OllamaIcon,
	OpenaiIcon,
	OpenRouterIcon,
	PerplexityIcon,
	QwenIcon,
	RecraftIcon,
	ReplicateIcon,
	RequestyIcon,
	SambaNovaIcon,
	TogetherAiIcon,
	VertexAiIcon,
	XaiIcon,
	XinferenceIcon,
	ZhipuIcon,
} from "@/components/icons/providers";
import { cn } from "@/lib/utils";

export const AUTO_PROVIDER_ICON_KEY = "AUTO";

type ProviderIcon = ComponentType<{ className?: string }>;

/**
 * Provider key (uppercased) → icon component. Aliases point at the same
 * component; `Cpu` stands in for CUSTOM and for anything unrecognised.
 */
const PROVIDER_ICONS = new Map<string, ProviderIcon>([
	["AI21", Ai21Icon],
	["ALIBABA_QWEN", QwenIcon],
	["ANTHROPIC", ClaudeIcon],
	["CLAUDE", ClaudeIcon],
	["ANYSCALE", AnyscaleIcon],
	["AZURE", AzureIcon],
	["AZURE_OPENAI", AzureIcon],
	["AWS_BEDROCK", BedrockIcon],
	["BEDROCK", BedrockIcon],
	["CEREBRAS", CerebrasIcon],
	["CLOUDFLARE", CloudflareIcon],
	["COHERE", CohereIcon],
	["COMETAPI", CometApiIcon],
	["CUSTOM", Cpu],
	["DATABRICKS", DatabricksIcon],
	["DEEPINFRA", DeepInfraIcon],
	["DEEPSEEK", DeepSeekIcon],
	["FIREWORKS_AI", FireworksAiIcon],
	["GOOGLE", GeminiIcon],
	["GITHUB_MODELS", GitHubModelsIcon],
	["GROQ", GroqIcon],
	["HUGGINGFACE", HuggingFaceIcon],
	["LM_STUDIO", LmStudioIcon],
	["MINIMAX", MiniMaxIcon],
	["MISTRAL", MistralIcon],
	["MOONSHOT", MoonshotIcon],
	["NSCALE", NscaleIcon],
	["OLLAMA", OllamaIcon],
	["OLLAMA_CHAT", OllamaIcon],
	["OPENAI", OpenaiIcon],
	["OPENROUTER", OpenRouterIcon],
	["PERPLEXITY", PerplexityIcon],
	["RECRAFT", RecraftIcon],
	["REPLICATE", ReplicateIcon],
	["REQUESTY", RequestyIcon],
	["SAMBANOVA", SambaNovaIcon],
	["TOGETHER_AI", TogetherAiIcon],
	["VERTEX_AI", VertexAiIcon],
	["XAI", XaiIcon],
	["XINFERENCE", XinferenceIcon],
	["ZHIPU", ZhipuIcon],
]);

/**
 * Returns a Lucide icon element for the given LLM / image-gen provider.
 * Accepts an optional `className` override for the icon size.
 */
export function getProviderIcon(
	provider: string,
	{ isAutoMode, className = "size-4" }: { isAutoMode?: boolean; className?: string } = {}
) {
	const providerKey = provider?.toUpperCase();

	if (isAutoMode || providerKey === AUTO_PROVIDER_ICON_KEY) {
		return <Shuffle className={cn(className, "text-muted-foreground")} />;
	}

	const Icon = providerKey ? PROVIDER_ICONS.get(providerKey) : undefined;

	if (!Icon) {
		return <Cpu className={cn(className, "text-muted-foreground")} />;
	}

	return <Icon className={cn(className)} />;
}
