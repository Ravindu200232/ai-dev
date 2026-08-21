import json, logging, re, requests
log = logging.getLogger("refiner")


SITE_TYPES = {
    "ecommerce":  ["online shop","online store","e-commerce","ecommerce","marketplace",
                   "catalogue","buy online","sell online","basket and checkout",
                   "sell my","take payments"],
    "portfolio":  ["portfolio","my work","showcase my work","personal website","resume site",
                   "cv site","my projects","show my skills"],
    "saas":       ["saas","b2b platform","subscription service","crm tool","analytics platform",
                   "team dashboard","business software"],
    "venue":      ["opening hours","visit us","find us","our location",
                   "directions","book a table","book an appointment",
                   "make a reservation","walk-in","price list",
                   "our menu","our services","our prices","our offerings",
                   "services and prices","what we offer","our team",
                   "gallery of our work","order for pickup"],
    "blog":       ["blog","article site","news site","magazine","journal","writing platform",
                   "content site","editorial"],
    "agency":     ["agency","design studio","creative studio","marketing agency",
                   "branding agency","advertising agency","web agency"],
    "startup":    ["startup","launch my startup","mvp","venture","fundraising","pre-launch",
                   "coming soon page"],
    "corporate":  ["corporate site","enterprise site","consulting firm","b2b company website",
                   "professional services site"],
    "landing":    ["landing page","waitlist","sign up page","coming soon","product launch page"],

    "tool":       ["calculator","converter","unit converter","currency converter",
                   "timer","stopwatch","clock","countdown","weather app","word counter",
                   "password generator","qr generator","color picker","bmi calculator",
                   "tax calculator","tip calculator","age calculator"],
    "game":       ["game","quiz game","trivia","puzzle","arcade","word game",
                   "memory game","snake game","tetris","chess","tic tac toe",
                   "card game","dice game","guessing game","play and score"],
    "app":        ["todo app","task manager","habit tracker","note taking","notes app",
                   "expense tracker","budget tracker","workout tracker","fitness tracker",
                   "mood tracker","journal app","reading list","bookmark manager",
                   "recipe app","meal planner","study planner","flashcard app",
                   "pomodoro","time tracker","project tracker","kanban","calendar app"],
    "dashboard":  ["dashboard","admin panel","analytics dashboard","stats dashboard",
                   "metrics","data visualization","monitoring panel","reporting tool"],
    "social":     ["social network","community platform","forum","chat app","messaging app",
                   "profile page","user feed","social media"],
    "general":    [],
}


KEYWORD_WEIGHTS = {
    "tool":  3,
    "game":  3,
    "app":   3,
    "dashboard": 3,
    "ecommerce": 2,
    "portfolio": 2,
    "venue": 2,
    "general": 0,
}

SECTION_MAP = {
    "ecommerce":  ["Hero","FeaturedProducts","Categories","Testimonials","Newsletter"],
    "portfolio":  ["Hero","About","Skills","Projects","Contact"],
    "saas":       ["Hero","Features","HowItWorks","Pricing","Testimonials","CTA"],
    "venue":      ["Hero","Offerings","About","Gallery","Visit"],
    "blog":       ["Hero","FeaturedPosts","Categories","Newsletter","Contact"],
    "agency":     ["Hero","Services","Work","Team","Contact"],
    "startup":    ["Hero","Problem","Solution","Features","Pricing","Contact"],
    "corporate":  ["Hero","About","Services","Team","Contact"],
    "landing":    ["Hero","Features","HowItWorks","Testimonials","CTA"],
    "tool":       ["App"],
    "game":       ["App"],
    "app":        ["App"],
    "dashboard":  ["App"],
    "social":     ["Hero","Feed","Profiles","Contact"],
    "general":    ["Hero","Features","About","Contact"],
}

# Types this file used to have, so a spec saved earlier still resolves.
LEGACY_TYPES = {"restaurant": "venue", "cafe": "venue", "food": "venue"}


def normalise_type(site_type: str) -> str:
    """One site type name, with the retired ones mapped to what replaced them."""
    key = str(site_type or "").lower().strip()
    return LEGACY_TYPES.get(key, key)


STRATEGY_MAP = {
    "tool": "react-app", "game": "react-app",
    "app":  "react-app", "dashboard": "react-app",
}


SYSTEM_PROMPT = """\
You are a senior product designer and React architect. Your job is to analyze a user's \
idea and produce a DETAILED, SPECIFIC build specification that a React developer can \
implement directly.

CRITICAL RULES:
1. Output ONLY a raw JSON object — no markdown, no explanation, no code blocks.
2. site_type MUST exactly match the user's idea. If they want a calculator app, \
site_type="tool". If they want a todo list, site_type="app". If they want a game, \
site_type="game". NEVER default to a type because it is a familiar one — pick the \
shape the idea actually has, and use "general" when none of them fits.
3. description must be 3-5 sentences describing exactly what the user asked for — \
be SPECIFIC. Include what the app does, how it works, and what makes it unique.
4. special_instructions must be highly detailed — describe exact UI components, \
interactions, data the app manages, visual style, color palette, animations.
5. key_features must list the ACTUAL features the user's specific app needs.
6. Do NOT suggest uninstalled npm packages. Only use: react, react-dom, framer-motion, \
react-icons/fi, react-icons/hi, react-icons/bi, tailwindcss, lucide-react.

Output JSON with these exact keys:
{
  "project_name": "kebab-case-name",
  "site_type": "tool|game|app|dashboard|ecommerce|portfolio|saas|venue|blog|agency|startup|corporate|landing|social|general",
  "title": "App/Site Title",
  "tagline": "One compelling sentence",
  "description": "3-5 sentences describing EXACTLY what was requested",
  "color_scheme": "Specific colors e.g. deep navy #0f172a with emerald #10b981 accents",
  "style": "modern|minimal|bold|playful|retro|corporate|luxury",
  "brand_name": "Brand Name",
  "target_audience": "Specific target users",
  "key_features": ["Specific feature 1", "Specific feature 2", "Specific feature 3", "Specific feature 4"],
  "component_details": "Describe in detail the main interactive components, state they manage, and UI layout",
  "special_instructions": "Detailed implementation notes: exact UI elements, interactions, data structures, visual effects"
}"""


class RefinerAgent:
    def __init__(self, ollama_url, model):
        self.url   = f"{ollama_url}/api/chat"
        self.model = model

    def refine(self, raw_idea):
        log.info(f"Refining idea with {self.model}...")

        kw_type = self._detect_type(raw_idea)
        log.info(f"   Keyword-detected type: {kw_type}")

        llm_data = self._llm_refine(raw_idea, kw_type)

        spec = self._build_spec(raw_idea, kw_type, llm_data)
        spec["strategy"] = STRATEGY_MAP.get(spec["site_type"], "react-sections")

        log.info(f"   Final: '{spec['title']}' | type={spec['site_type']} | strategy={spec['strategy']}")
        log.info(f"   Sections: {spec['sections']}")
        log.info(f"   Features: {spec['key_features']}")
        return json.dumps(spec, indent=2)

    @staticmethod
    def _stem(word: str) -> str:
        """`tracking`/`trackers` -> `track`, so wording does not have to match."""
        w = str(word or "").lower()
        for end in ("ing", "ers", "er", "es", "s"):
            if w.endswith(end) and len(w) - len(end) >= 3:
                return w[:-len(end)]
        return w

    def _phrase_hit(self, phrase: str, stems: set, text: str) -> int:
        """2 for the exact phrase, 1 when its words are all there, else 0."""
        if phrase in text:
            return 2
        parts = [self._stem(p) for p in phrase.split()]
        return 1 if len(parts) > 1 and all(p in stems for p in parts) else 0

    def _detect_type(self, idea: str) -> str:
        """Score every type by how much of its wording the idea really uses."""
        il = idea.lower()
        stems = {self._stem(w) for w in re.findall(r"[a-z0-9]+", il)}
        scores: dict[str, int] = {}
        for site_type, keywords in SITE_TYPES.items():
            if not keywords:
                continue
            matches = sum(self._phrase_hit(k, stems, il) for k in keywords)
            if matches > 0:
                weight = KEYWORD_WEIGHTS.get(site_type, 1)
                scores[site_type] = matches * weight

        if not scores:
            return "general"

        best = max(scores, key=lambda k: scores[k])
        log.info(f"   Type scores: {dict(sorted(scores.items(), key=lambda x: -x[1]))}")
        return best

    def _llm_refine(self, raw_idea: str, kw_type: str) -> dict:
        """Call the LLM with the idea AND the keyword-detected type as a hint."""
        hint = (
            f"\nIMPORTANT: The keyword analysis suggests site_type='{kw_type}'. "
            f"Only override this if the user's idea clearly implies something different."
            if kw_type != "general"
            else ""
        )

        user_msg = (
            f"User idea: {raw_idea}\n"
            f"{hint}\n\n"
            f"Analyze this carefully and produce a detailed JSON spec. "
            f"Be SPECIFIC to what the user asked for. Output JSON only:"
        )

        try:
            resp = requests.post(self.url, json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                "stream": False,
                "options": {
                    "temperature": 0.15,
                    "num_predict": 800,
                    "top_p": 0.9,
                }
            }, timeout=90)
            resp.raise_for_status()
            content = resp.json()["message"]["content"].strip()

            if "```" in content:
                m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
                content = m.group(1) if m else content.split("```")[1]

            s, e = content.find("{"), content.rfind("}")
            if s != -1 and e != -1:
                parsed = json.loads(content[s:e+1])
                log.info(f"   LLM returned type: {parsed.get('site_type', '?')}")

                try:
                    import server as _srv
                    r_data = resp.json()
                    _srv._add_tokens(
                        r_data.get("prompt_eval_count", 0),
                        r_data.get("eval_count", 0),
                    )
                except Exception:
                    pass
                return parsed

        except json.JSONDecodeError as ex:
            log.warning(f"LLM returned invalid JSON ({ex}) — using keyword detection")
        except Exception as ex:
            log.warning(f"LLM refine failed ({ex}) — using keyword detection")

        return {}

    def _build_spec(self, raw_idea: str, kw_type: str, llm_data: dict) -> dict:
        """Merge keyword detection + LLM output into a final spec."""
        llm_type = normalise_type(llm_data.get("site_type", ""))
        kw_type = normalise_type(kw_type)

        high_confidence_kw = kw_type in ("tool", "game", "app", "dashboard")

        if llm_type in SECTION_MAP and not (high_confidence_kw and llm_type != kw_type):
            final_type = llm_type
        elif kw_type in SECTION_MAP:
            final_type = kw_type
            log.info(f"   Overriding LLM type '{llm_type}' with keyword type '{kw_type}'")
        else:
            final_type = "general"

        brand = (
            llm_data.get("brand_name")
            or llm_data.get("title")
            or self._extract_name(raw_idea)
        )
        project_name = re.sub(r'[^a-z0-9]+', '-', brand.lower())[:30].strip('-') or "project"

        description = llm_data.get("description") or raw_idea[:400]

        llm_instructions = llm_data.get("special_instructions", "")
        component_details = llm_data.get("component_details", "")
        if component_details and component_details not in llm_instructions:
            combined_instructions = f"{llm_instructions}\n\nComponent details: {component_details}".strip()
        else:
            combined_instructions = llm_instructions or raw_idea

        if len(combined_instructions.strip()) < 50:
            combined_instructions = f"{raw_idea}\n\n{combined_instructions}".strip()

        features = llm_data.get("key_features", [])

        if isinstance(features, list):
            features = [str(f) for f in features if f][:8]
        else:
            features = []

        return {
            "project_name":         project_name,
            "site_type":            final_type,
            "strategy":             STRATEGY_MAP.get(final_type, "react-sections"),
            "title":                llm_data.get("title", brand),
            "tagline":              llm_data.get("tagline", f"Welcome to {brand}"),
            "description":          description,
            "color_scheme":         llm_data.get("color_scheme", "dark with cyan and purple accents"),
            "style":                llm_data.get("style", "modern"),
            "brand_name":           brand,
            "target_audience":      llm_data.get("target_audience", "Everyone"),
            "key_features":         features,
            "component_details":    component_details,
            "special_instructions": combined_instructions,
            "sections":             SECTION_MAP.get(final_type, SECTION_MAP["general"]),

            "design": {
                "style":        llm_data.get("style", "modern"),
                "color_scheme": llm_data.get("color_scheme", "dark with cyan and purple accents"),
            },
            "features": features,

            "_raw_idea": raw_idea,
        }

    def _extract_name(self, idea: str) -> str:
        stop = {
            "a","an","the","build","create","make","i","want","need","please",
            "with","for","and","or","of","website","site","page","web","app",
            "simple","basic","nice","cool","good","great","some","just","like",
        }
        words = [w.strip(".,!?\"'") for w in idea.split() if w.lower().strip(".,!?\"'") not in stop]
        return " ".join(words[:3]).title() if words else "My App"
