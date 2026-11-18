import assert from "node:assert/strict";
import test from "node:test";
import {
	FLASHCARDS_MAX_CARDS,
	FLASHCARDS_MIN_CARDS,
	FlashcardDeckSchema,
} from "@/features/artifacts/rendering/formats/flashcards/schema";
import {
	QUIZ_MAX_QUESTIONS,
	QUIZ_MIN_QUESTIONS,
	QuizSchema,
} from "@/features/artifacts/rendering/formats/quiz/schema";

type Card = {
	front_text: string;
	back_text: string;
	hint_text?: string;
	[key: string]: unknown;
};

type Question = {
	question_text: string;
	options: string[];
	correct_option_index: number;
	explanation_text: string;
	[key: string]: unknown;
};

function deck(overrides: { title?: string; cards?: Card[] } = {}): {
	schema_version: number;
	title: string;
	cards: Card[];
} {
	return {
		schema_version: 1,
		title: "Compact deck",
		cards: Array.from({ length: FLASHCARDS_MIN_CARDS }, (_, index) => ({
			front_text: `Card ${index}`,
			back_text: `Back ${index}`,
		})),
		...overrides,
	};
}

function quiz(overrides: { title?: string; questions?: Question[] } = {}) {
	return {
		schema_version: 1,
		title: "Compact quiz",
		questions: Array.from({ length: QUIZ_MIN_QUESTIONS }, (_, index) => ({
			question_text: `Question ${index}`,
			options: ["A", "B", "C", "D"].map((option) => `${option} ${index}`),
			correct_option_index: index % 4,
			explanation_text: `Explanation ${index}`,
		})),
		...overrides,
	};
}

type ParseResult = {
	success: boolean;
	error?: { issues: { path: PropertyKey[]; message: string }[] };
};

/** Every issue a schema reports, as "path: message", so two schemas can be compared line by line. */
function issues(result: ParseResult): string[] {
	if (result.success) return [];
	return (result.error?.issues ?? []).map((issue) => `${issue.path.join(".")}: ${issue.message}`);
}

test("flashcard and quiz schemas accept a valid sample", () => {
	assert.deepEqual(issues(FlashcardDeckSchema.safeParse(deck())), []);
	assert.deepEqual(issues(QuizSchema.safeParse(quiz())), []);
});

test("both schemas reject a title that is empty, long, multiline or holds control characters", () => {
	const cases: [string, string][] = [
		["", "title: Title must not be empty"],
		["   ", "title: Title must not be empty"],
		["x".repeat(201), "title: Title is too long"],
		["line\nbreak", "title: Title contains unsupported content"],
		["carriage\rreturn", "title: Title contains unsupported content"],
		["bell\u0007", "title: Title contains unsupported content"],
		["delete\u007f", "title: Title contains unsupported content"],
	];
	for (const [title, expected] of cases) {
		assert.deepEqual(
			issues(FlashcardDeckSchema.safeParse(deck({ title }))),
			[expected],
			`flashcards title ${JSON.stringify(title)}`
		);
		assert.deepEqual(
			issues(QuizSchema.safeParse(quiz({ title }))),
			[expected],
			`quiz title ${JSON.stringify(title)}`
		);
	}
});

test("both schemas count title length in code points", () => {
	const emoji = "\u{1f600}";
	assert.deepEqual(issues(FlashcardDeckSchema.safeParse(deck({ title: emoji.repeat(200) }))), []);
	assert.deepEqual(issues(QuizSchema.safeParse(quiz({ title: emoji.repeat(200) }))), []);
	assert.deepEqual(issues(FlashcardDeckSchema.safeParse(deck({ title: emoji.repeat(201) }))), [
		"title: Title is too long",
	]);
	assert.deepEqual(issues(QuizSchema.safeParse(quiz({ title: emoji.repeat(201) }))), [
		"title: Title is too long",
	]);
});

test("both schemas bound their first text field the same way", () => {
	const cases: [string, string, string][] = [
		[
			"",
			"cards.0.front_text: Front must not be empty",
			"questions.0.question_text: Question must not be empty",
		],
		[
			"x".repeat(4001),
			"cards.0.front_text: Front is too long",
			"questions.0.question_text: Question is too long",
		],
		[
			"bell\u0007",
			"cards.0.front_text: Front has control characters",
			"questions.0.question_text: Question has control characters",
		],
		[
			"orphan \\)",
			"cards.0.front_text: Front has invalid LaTeX delimiters",
			"questions.0.question_text: Question has invalid LaTeX delimiters",
		],
	];
	for (const [value, flashcardIssue, quizIssue] of cases) {
		const cards = deck().cards;
		cards[0] = { front_text: value, back_text: "Back 0" };
		assert.deepEqual(issues(FlashcardDeckSchema.safeParse(deck({ cards }))), [flashcardIssue]);

		const questions = quiz().questions;
		questions[0] = { ...questions[0], question_text: value };
		assert.deepEqual(issues(QuizSchema.safeParse(quiz({ questions }))), [quizIssue]);
	}
});

test("both schemas accept text at the length limit and valid LaTeX", () => {
	const cards = deck().cards;
	cards[0] = { front_text: `${"x".repeat(3992)} \\(x+1\\)`, back_text: "Back 0" };
	assert.deepEqual(issues(FlashcardDeckSchema.safeParse(deck({ cards }))), []);

	const questions = quiz().questions;
	questions[0] = { ...questions[0], question_text: `${"x".repeat(3992)} \\(x+1\\)` };
	assert.deepEqual(issues(QuizSchema.safeParse(quiz({ questions }))), []);
});

test("both schemas report a duplicate first text field after Unicode normalization", () => {
	const cards = deck().cards;
	cards[0] = { front_text: "Ａ   TERM", back_text: "Back 0" };
	cards[cards.length - 1] = { front_text: "a term", back_text: "Back last" };
	assert.deepEqual(issues(FlashcardDeckSchema.safeParse(deck({ cards }))), [
		`cards.${cards.length - 1}.front_text: Duplicate card front`,
	]);

	const questions = quiz().questions;
	questions[0] = { ...questions[0], question_text: "Ａ   TERM" };
	questions[questions.length - 1] = {
		...questions[questions.length - 1],
		question_text: "a term",
	};
	assert.deepEqual(issues(QuizSchema.safeParse(quiz({ questions }))), [
		`questions.${questions.length - 1}.question_text: Duplicate question`,
	]);
});

test("both schemas enforce their item count and reject unknown keys", () => {
	const shortDeck = deck().cards.slice(1);
	assert.equal(FlashcardDeckSchema.safeParse(deck({ cards: shortDeck })).success, false);
	const longDeck = Array.from({ length: FLASHCARDS_MAX_CARDS + 1 }, (_, index) => ({
		front_text: `Card ${index}`,
		back_text: `Back ${index}`,
	}));
	assert.equal(FlashcardDeckSchema.safeParse(deck({ cards: longDeck })).success, false);
	assert.equal(FlashcardDeckSchema.safeParse({ ...deck(), extra: true }).success, false);
	const deckWithExtraCardKey = deck().cards;
	deckWithExtraCardKey[0] = { ...deckWithExtraCardKey[0], extra: true };
	assert.equal(FlashcardDeckSchema.safeParse(deck({ cards: deckWithExtraCardKey })).success, false);

	const shortQuiz = quiz().questions.slice(1);
	assert.equal(QuizSchema.safeParse(quiz({ questions: shortQuiz })).success, false);
	const longQuiz = Array.from({ length: QUIZ_MAX_QUESTIONS + 1 }, (_, index) => ({
		question_text: `Question ${index}`,
		options: ["A", "B", "C", "D"].map((option) => `${option} ${index}`),
		correct_option_index: index % 4,
		explanation_text: `Explanation ${index}`,
	}));
	assert.equal(QuizSchema.safeParse(quiz({ questions: longQuiz })).success, false);
	assert.equal(QuizSchema.safeParse({ ...quiz(), extra: true }).success, false);
});

test("the quiz schema keeps its four distinct options and its answer range", () => {
	const questions = quiz().questions;
	questions[0] = { ...questions[0], options: ["Same", " same ", "C", "D"] };
	assert.deepEqual(issues(QuizSchema.safeParse(quiz({ questions }))), [
		"questions.0.options: Options must be distinct",
	]);

	const threeOptions = quiz().questions;
	threeOptions[0] = { ...threeOptions[0], options: ["A", "B", "C"] };
	assert.equal(QuizSchema.safeParse(quiz({ questions: threeOptions })).success, false);

	const outOfRange = quiz().questions;
	outOfRange[0] = { ...outOfRange[0], correct_option_index: 4 };
	assert.equal(QuizSchema.safeParse(quiz({ questions: outOfRange })).success, false);

	const emptyExplanation = quiz().questions;
	emptyExplanation[0] = { ...emptyExplanation[0], explanation_text: "" };
	assert.deepEqual(issues(QuizSchema.safeParse(quiz({ questions: emptyExplanation }))), [
		"questions.0.explanation_text: Explanation must not be empty",
	]);
});
