import { z } from "zod";

function codePointLength(value: string): number {
	return [...value].length;
}

function hasUnsupportedControlCharacter(value: string): boolean {
	for (const character of value) {
		const codePoint = character.codePointAt(0) ?? 0;
		if (
			codePoint === 0x7f ||
			(codePoint >= 0 && codePoint <= 0x08) ||
			codePoint === 0x0b ||
			codePoint === 0x0c ||
			(codePoint >= 0x0e && codePoint <= 0x1f)
		) {
			return true;
		}
	}
	return false;
}

/** A study text field: non-empty, bounded by code points, plain, and parseable as LaTeX. */
export function boundedText(name: string, maximum: number, parseText: (value: string) => unknown) {
	return z
		.string()
		.refine((value) => value.trim().length > 0, `${name} must not be empty`)
		.refine((value) => codePointLength(value) <= maximum, `${name} is too long`)
		.refine((value) => !hasUnsupportedControlCharacter(value), `${name} has control characters`)
		.refine((value) => parseText(value) !== null, `${name} has invalid LaTeX delimiters`);
}

/** NFKC-normalized, case-folded, whitespace-collapsed text, used to spot duplicates. */
export function normalizeStudyText(value: string): string {
	let normalized = "";
	let pendingSpace = false;
	for (const character of value.normalize("NFKC").toLocaleLowerCase().trim()) {
		if (character.trim() === "") {
			pendingSpace = normalized.length > 0;
		} else {
			if (pendingSpace) normalized += " ";
			normalized += character;
			pendingSpace = false;
		}
	}
	return normalized;
}

/** The deck or quiz title: one short, single-line, plain line. */
export const studyTitleSchema = z
	.string()
	.refine((value) => value.trim().length > 0, "Title must not be empty")
	.refine((value) => codePointLength(value) <= 200, "Title is too long")
	.refine(
		(value) =>
			!value.includes("\n") && !value.includes("\r") && !hasUnsupportedControlCharacter(value),
		"Title contains unsupported content"
	);
