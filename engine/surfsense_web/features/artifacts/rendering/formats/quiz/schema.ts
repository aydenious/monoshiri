import { z } from "zod";
import { boundedText, normalizeStudyText, studyTitleSchema } from "../study-schema";
import { parseStudyText } from "../study-text/parse-text";

export const QUIZ_MAX_VIEWER_BYTES = 15 * 1024 * 1024;
export const QUIZ_MIN_QUESTIONS = 5;
export const QUIZ_MAX_QUESTIONS = 30;

export const QuizQuestionSchema = z
	.object({
		question_text: boundedText("Question", 4_000, parseStudyText),
		options: z
			.array(boundedText("Option", 4_000, parseStudyText))
			.length(4)
			.refine(
				(options) => new Set(options.map(normalizeStudyText)).size === options.length,
				"Options must be distinct"
			),
		correct_option_index: z.number().int().min(0).max(3),
		explanation_text: boundedText("Explanation", 12_000, parseStudyText),
	})
	.strict();

export const QuizSchema = z
	.object({
		schema_version: z.literal(1),
		title: studyTitleSchema,
		questions: z.array(QuizQuestionSchema).min(QUIZ_MIN_QUESTIONS).max(QUIZ_MAX_QUESTIONS),
	})
	.strict()
	.superRefine((quiz, context) => {
		const seen = new Set<string>();
		quiz.questions.forEach((question, index) => {
			const normalized = normalizeStudyText(question.question_text);
			if (seen.has(normalized)) {
				context.addIssue({
					code: "custom",
					path: ["questions", index, "question_text"],
					message: "Duplicate question",
				});
			}
			seen.add(normalized);
		});
	});

export type Quiz = z.infer<typeof QuizSchema>;
