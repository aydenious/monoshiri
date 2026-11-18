import { z } from "zod";
import { boundedText, normalizeStudyText, studyTitleSchema } from "../study-schema";
import { parseFlashcardText } from "./parse-text";

export const FLASHCARDS_MAX_VIEWER_BYTES = 15 * 1024 * 1024;
export const FLASHCARDS_MIN_CARDS = 15;
export const FLASHCARDS_MAX_CARDS = 100;

export const FlashcardSchema = z
	.object({
		front_text: boundedText("Front", 4_000, parseFlashcardText),
		back_text: boundedText("Back", 12_000, parseFlashcardText),
		hint_text: boundedText("Hint", 2_000, parseFlashcardText).optional(),
	})
	.strict();

export const FlashcardDeckSchema = z
	.object({
		schema_version: z.literal(1),
		title: studyTitleSchema,
		cards: z.array(FlashcardSchema).min(FLASHCARDS_MIN_CARDS).max(FLASHCARDS_MAX_CARDS),
	})
	.strict()
	.superRefine((deck, context) => {
		const seen = new Set<string>();
		deck.cards.forEach((card, index) => {
			const normalized = normalizeStudyText(card.front_text);
			if (seen.has(normalized)) {
				context.addIssue({
					code: "custom",
					path: ["cards", index, "front_text"],
					message: "Duplicate card front",
				});
			}
			seen.add(normalized);
		});
	});

export type FlashcardDeck = z.infer<typeof FlashcardDeckSchema>;
