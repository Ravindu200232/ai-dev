package srs

import (
	"strings"
	"testing"
)

func TestSelfExplanatory(t *testing.T) {
	options := []Option{{Label: "Card payments", Value: "card"}, {Label: "Cash", Value: "cash"}}
	cases := map[string]bool{
		"":                                  true,
		"Card payments":                     true, // one of the options offered
		"cash":                              true, // matched on the value
		"none":                              true, // filler
		"N/A":                               true,
		"we take card and cash at the till": true,  // a whole phrase
		"loyalty":                           false, // one word, ambiguous
		"quick book":                        false, // two words
		"???":                               false,
	}
	for text, want := range cases {
		if got := SelfExplanatory(text, options); got != want {
			t.Errorf("SelfExplanatory(%q) = %v, want %v", text, got, want)
		}
	}
	if !NeedsClarification("loyalty", options) {
		t.Error("a one-word answer has to be read back")
	}
	if NeedsClarification("", options) {
		t.Error("nothing typed is nothing to clarify")
	}
}

func TestClarificationSkipsTheObvious(t *testing.T) {
	svc := testService(t)
	state := svc.StartClarification(t.Context(), "offerings",
		"we sell coffee and pastries all day", "offerings", "What do you sell?",
		nil, nil, "prj_1", "", "English")

	if state.Status != ClarifyConfirmed || !state.Ready() {
		t.Fatalf("a clear answer must not be questioned: %+v", state)
	}
	if state.ClarifyQuestion() != nil {
		t.Error("nothing to ask")
	}
	resolved := state.Resolve()
	if resolved == nil || resolved.Text != "we sell coffee and pastries all day" {
		t.Errorf("resolved = %+v", resolved)
	}
}

func TestClarificationReadsBackAShortAnswer(t *testing.T) {
	svc := testService(t)
	// With no model reachable there is no reading, so the answer stands as
	// written rather than the customer being asked a question nobody can
	// answer usefully.
	state := svc.StartClarification(t.Context(), "offerings", "loyalty", "offerings",
		"What else should it do?", nil, nil, "prj_1", "", "English")
	if !state.Ready() {
		t.Fatalf("without a model the answer is taken as written: %+v", state)
	}
	if state.Meaning != "loyalty" {
		t.Errorf("meaning = %q", state.Meaning)
	}
}

func TestClarificationSteps(t *testing.T) {
	svc := testService(t)
	state := &Clarification{
		QuestionID: "offerings", Topic: "offerings", Raw: "loyalty",
		Status: ClarifyNeedsMeaning, Language: "English",
		Suggestions: []Option{
			{Label: "A points card customers collect stamps on", Value: "offerings_points_card"},
			{Label: "A discount for regulars", Value: "regular_discount"},
		},
	}

	q := state.ClarifyQuestion()
	if q == nil || q.Kind != "single" || q.AnswerType != "single_choice" {
		t.Fatalf("question = %+v", q)
	}
	if !strings.Contains(q.Question, "loyalty") {
		t.Errorf("the customer's own words must be quoted back: %q", q.Question)
	}
	if len(q.Options) != 4 {
		t.Fatalf("two readings plus keep and retype: %+v", q.Options)
	}
	if q.Options[2].OptionValue() != keepOriginal || q.Options[3].OptionValue() != typeAnother {
		t.Errorf("the escape hatches are missing: %+v", q.Options)
	}
	if q.ID != "clarify:offerings" {
		t.Errorf("id = %q", q.ID)
	}

	// Picking a reading moves on to why they want it.
	svc.AnswerClarification(t.Context(), state, "offerings_points_card", "", nil, "")
	if state.Status != ClarifyNeedsPurpose || !state.MeaningConfirmed {
		t.Fatalf("state = %+v", state)
	}
	if state.Meaning != "A points card customers collect stamps on" {
		t.Errorf("meaning = %q", state.Meaning)
	}
	// The model answered with a namespaced slug, so the customer's own word
	// is kept rather than an identifier nobody typed.
	if state.Answer != "loyalty" {
		t.Errorf("answer = %q", state.Answer)
	}
	if state.Resolve() != nil {
		t.Error("nothing is a requirement until the purpose is confirmed too")
	}

	q = state.ClarifyQuestion()
	if q == nil || q.Kind != "text" || !strings.Contains(q.Question, "points card") {
		t.Fatalf("question = %+v", q)
	}

	// With no model to check it, the reason is taken as given.
	svc.AnswerClarification(t.Context(), state, nil, "so regulars come back more often", nil, "")
	if state.Status != ClarifyConfirmPurpose {
		t.Fatalf("state = %+v", state)
	}
	q = state.ClarifyQuestion()
	if q == nil || q.Kind != "yes_no" || len(q.Options) != 2 {
		t.Fatalf("question = %+v", q)
	}

	svc.AnswerClarification(t.Context(), state, true, "", nil, "")
	if state.Status != ClarifyConfirmed || !state.Ready() {
		t.Fatalf("state = %+v", state)
	}
	resolved := state.Resolve()
	if resolved == nil || resolved.Answer != "loyalty" ||
		resolved.Purpose != "so regulars come back more often" {
		t.Errorf("resolved = %+v", resolved)
	}
}

func TestClarificationCanBeSentBack(t *testing.T) {
	svc := testService(t)
	state := &Clarification{
		QuestionID: "offerings", Raw: "loyalty", Status: ClarifyConfirmPurpose,
		Meaning: "A points card", Purpose: "because", MeaningConfirmed: true,
	}
	svc.AnswerClarification(t.Context(), state, false, "", nil, "")
	if state.Status != ClarifyNeedsPurpose || state.PurposeConfirmed {
		t.Errorf("saying no must reopen the question: %+v", state)
	}
	if state.Resolve() != nil {
		t.Error("an unconfirmed purpose is not a requirement")
	}
}

func TestDuplicateFolding(t *testing.T) {
	state := &Clarification{
		QuestionID: "offerings", Raw: "points card", Status: ClarifyDuplicate,
		DuplicateOf: "A loyalty points card", MeaningConfirmed: true, PurposeConfirmed: true,
	}
	resolved := state.Resolve()
	if resolved == nil || resolved.MergedInto != "A loyalty points card" {
		t.Errorf("resolved = %+v", resolved)
	}
	if state.Open() {
		t.Error("a duplicate is settled, not open")
	}
}

func TestMatchesExistingIgnoresWordOrder(t *testing.T) {
	existing := []string{"A loyalty points card", "Table booking"}
	if got := matchesExisting("points card loyalty a", existing); got != "A loyalty points card" {
		t.Errorf("matchesExisting = %q", got)
	}
	if got := matchesExisting("something else", existing); got != "" {
		t.Errorf("matchesExisting = %q", got)
	}
}

func TestCleanAnswer(t *testing.T) {
	cases := []struct{ value, id, raw, want string }{
		{"offerings_points_card", "offerings", "loyalty", "loyalty"},
		{"offerings:A points card", "offerings", "loyalty", "A points card"},
		{"“A points card”", "offerings", "loyalty", "A points card"},
		{"card", "offerings", "loyalty", "card"},
		// A human-readable value survives even when it is namespaced.
		{"offerings:A points card", "offerings", "", "A points card"},
	}
	for _, tc := range cases {
		if got := cleanAnswer(tc.value, tc.id, tc.raw); got != tc.want {
			t.Errorf("cleanAnswer(%q) = %q, want %q", tc.value, got, tc.want)
		}
	}
}

func TestPendingClarificationRoundTripsThroughTheSession(t *testing.T) {
	session := &Session{}
	storeClarification(session, "offerings", &Clarification{
		QuestionID: "offerings", Raw: "loyalty", Status: ClarifyNeedsPurpose,
		Meaning: "A points card",
	})
	storeClarification(session, "settled", &Clarification{
		QuestionID: "settled", Status: ClarifyConfirmed,
		MeaningConfirmed: true, PurposeConfirmed: true,
	})

	key, state := PendingClarification(session)
	if key != "offerings" || state == nil {
		t.Fatalf("pending = %q %+v", key, state)
	}
	if state.Meaning != "A points card" {
		t.Errorf("the state did not survive the round trip: %+v", state)
	}

	storeClarification(session, "offerings", &Clarification{Status: ClarifyConfirmed,
		MeaningConfirmed: true, PurposeConfirmed: true})
	if key, _ := PendingClarification(session); key != "" {
		t.Errorf("nothing should be pending: %q", key)
	}
}
