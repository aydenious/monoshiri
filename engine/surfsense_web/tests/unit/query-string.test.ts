import assert from "node:assert/strict";
import test from "node:test";
import { toQueryString } from "../../lib/apis/query-string";

test("toQueryString is empty without params", () => {
	assert.equal(toQueryString(undefined), "");
});

test("toQueryString stringifies scalars and joins arrays with commas", () => {
	assert.equal(
		toQueryString({ skip: 0, search: "a b", document_type: ["FILE", "NOTE"] }),
		"skip=0&search=a+b&document_type=FILE%2CNOTE"
	);
});
