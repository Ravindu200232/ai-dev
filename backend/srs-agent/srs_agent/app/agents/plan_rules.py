"""Prompt and validation rules for the approval plan."""
from __future__ import annotations

import re


_SYS = """You are a product architect writing a plan for a NON-TECHNICAL \
customer to approve. You are given everything one customer asked for, and you \
produce one complete plan of the application that answers it.

Model what people are trying to DO before you decide what screens exist. A \
workflow is a whole job, start to finish: who starts it, what they see, what \
they change, and where they end up. Screens are then whatever those workflows \
need — nothing more.

This matters because the usual failure is to reach for the shape of the \
category. A shop gets a storefront, a cart and an admin table; a business tool \
gets a dashboard, a list and a form. Those are not requirements, they are \
habits, and an app built from them fits nobody.

This plan is a CEILING, not a summary. Everything written afterwards is derived \
from it and nothing may exceed it: the specification's tables come from your \
`records` and nothing else, its roles from your `users`, its pages from your \
`screens`, and every one of its requirements from your `users`, `features` and \
`workflows`. A record you leave out cannot be added later; a screen you forget \
is a screen the finished app does not have. Say the whole thing here.

Rules:
- Every screen carries a `purpose` naming the job it exists for. If the only \
honest purpose is "applications like this usually have one", do not create it.
- Every record lists at least two things it `keeps`. A record with no fields \
becomes an empty table. Write plain labels — "Price", not "Price (money)" — \
because these become field names literally.
- `workflows` are the USER JOURNEYS, and there is one for EVERY kind of person \
in `users` — not only the customer's. Each runs a whole job start to finish, in \
three steps or more, and names the screens it passes through: who starts it \
(`who` is their role), what they see, what they change, where they end up. A \
shop's owner has a journey ("Restocking a title": Admin Books → edit stock → \
back to the list) exactly as its customer does ("Buying a book": Catalogue → \
Book Detail → Cart → Checkout → Order History). A role with no journey is a \
role whose half of the app nobody has thought through.
- Where people sign in, each of them gets at least two `can_do` lines.
- Ask an open question rather than inventing an answer. Mark it required when \
the app cannot be specified without it. Give every question 2-4 `options`: the \
answers you would accept, each one short, concrete and a real alternative to \
the others. A question that offers "coupon codes" and "a field on the order" \
can be settled with one click; the same question with no options gets answered \
"yes", which settles nothing and asks it again next round. Leave `options` out \
only where no list could be right — a name, a number, a free description.
- Use only the records, roles and actions the answers support. Do not add a \
record because the domain usually has one. The customer's stated main success \
outcome is a hard requirement: represent it explicitly in `product_intent` and \
in at least one workflow or feature so it can become an acceptance proof.
- Plain language throughout. No jargon: say "records" not "entities", "log in" \
not "authentication", "pages" not "routes".
- If accounts exist, `account_policy` is mandatory. State exactly who may create an account, the ONE role public sign-up creates, and where sign-in/sign-up live. For admin-created, invite, or request-access modes, name the `provisioning_role`. Never let a public form choose Admin/Manager/Staff.
- Screen `who` is an access boundary. Public screens use Visitor/Everyone only; protected screens list only the roles that may open them. Do not put every role on every screen.

Return ONLY a JSON object:
{
  "app_name": "what the thing is called — return it only if you are changing it",
  "product_intent": "one paragraph: what this is and who it is for",
  "users": [{"role": "Cashier", "can_do": ["short sentence", ...]}],
  "screens": [{"name": "Sale Terminal", "route": "/sale", "purpose": "why it exists", "who": ["Cashier"]}],
  "records": [{"name": "Product", "keeps": ["Name", "Price", ...]}],
  "workflows": [{"name": "Taking a sale", "who": "Cashier",
                 "steps": ["Cashier opens the Sale Terminal", "...", "..."]}],
  "features": ["short sentence describing ONE thing the software does", ...],
  "account_policy": {
    "accounts_required": true,
    "sign_in_fields": ["email", "password"],
    "registration_fields": ["full_name", "email", "password"],
    "registration_mode": "open | admin_created | invite | request | none",
    "registration_role": "Customer or null",
    "provisioning_role": "Admin or null",
    "sign_in_route": "/login or null",
    "sign_up_route": "/register or null"
  },
  "look_and_feel": "one or two sentences on theme, colour and devices",
  "assumptions": ["something we decided for them, stated plainly"],
  "open_questions": [{"question": "...", "required": true,
                      "options": ["one way it could go", "the other way", ...]}]
}"""

_REVISION_TAIL = (
    "Do exactly what they asked for, and nothing else. A revision is not a "
    "re-plan: do not rewrite wording you were not asked about, do not drop "
    "screens, records, workflows or features that are already in the plan "
    "above, and do not re-derive the plan from the answers — those are context "
    "for understanding the request, not the plan.\n"
    "Return the complete revised plan. Any section you leave out of your JSON "
    "is kept exactly as it stands above, so it is safe to return only the "
    "sections you actually changed — but a section you DO return replaces that "
    "section outright, so return it in full, including the items you are "
    "keeping.\n"
    "`open_questions` is the exception you must always return. Drop every "
    "question their message answers — an answer that picks one of the options "
    "you offered settles that question completely — and keep the ones it does "
    "not touch. Return `[]` when nothing is left to ask; an empty list is a "
    "valid and expected answer, and it is what lets them approve the plan. Do "
    "NOT raise new questions on a revision: they came here to settle the plan, "
    "and a round that answers two questions and asks two more never ends."
)


def _plan_validator(pack: dict | None = None):
    """A depth floor, sized to the kind of app this is."""
    pack = pack or {}
    archetype = str(pack.get("archetype") or "")
    thin = archetype in ("landing-single-page", "single-tool", "marketing")
    wants_auth = str(pack.get("auth_policy") or "") == "required"

    def validate(d: dict) -> None:
        if not isinstance(d, dict):
            raise ValueError("plan must be a JSON object")

        intent = str(d.get("product_intent") or "").strip()
        if len(intent) < 120:
            raise ValueError(
                "'product_intent' must be a full paragraph saying what this is "
                "and who it is for — one line is not enough for someone to "
                "approve")

        screens = [s for s in (d.get("screens") or [])
                   if isinstance(s, dict) and str(s.get("name") or "").strip()]
        if not screens:
            raise ValueError("provide at least one screen, each with a 'name' "
                             "and a 'purpose'")

        blank = [s["name"] for s in screens if not str(s.get("purpose") or "").strip()]
        if blank:
            raise ValueError(
                f"every screen needs a 'purpose' naming the job it exists for; "
                f"missing on: {', '.join(str(b) for b in blank[:5])}")

        feats = [f for f in (d.get("features") or []) if str(f).strip()]
        if not feats:
            raise ValueError("provide the product capabilities the customer actually requested; "
                             "do not invent features to satisfy a count")

        if thin:
            return

        records = [r for r in (d.get("records") or [])
                   if isinstance(r, dict) and str(r.get("name") or "").strip()]
        if not records:
            raise ValueError("this record-based app needs at least one named business record; "
                             "use only records supported by the customer's answers")
        empty = [r["name"] for r in records
                 if not [k for k in (r.get("keeps") or []) if str(k).strip()]]
        if empty:
            raise ValueError(
                f"every record needs at least 2 things it 'keeps'; empty on: "
                f"{', '.join(str(e) for e in empty[:5])}")
        shallow = [r["name"] for r in records
                   if len([k for k in (r.get("keeps") or []) if str(k).strip()]) < 2]
        if shallow:
            raise ValueError(
                f"these records keep only one thing, which is not a record: "
                f"{', '.join(str(s) for s in shallow[:5])}")

        flows = [w for w in (d.get("workflows") or [])
                 if isinstance(w, dict)
                 and len([s for s in (w.get("steps") or []) if str(s).strip()]) >= 2]
        if not flows:
            raise ValueError("provide at least one 'workflow' with 2 or more "
                             "steps — a whole job from start to finish")

        if wants_auth:
            able = [u for u in (d.get("users") or [])
                    if isinstance(u, dict)
                    and len([c for c in (u.get("can_do") or []) if str(c).strip()]) >= 2]
            if not able:
                raise ValueError("this app has people who sign in, so at least "
                                 "one entry in 'users' needs 2 or more 'can_do' "
                                 "lines")
            policy = d.get("account_policy")
            if not isinstance(policy, dict) or not policy.get("accounts_required"):
                raise ValueError("account_policy is required for an app with sign-in")
            mode = str(policy.get("registration_mode") or "").strip()
            if mode not in {"open", "admin_created", "invite", "request", "none"}:
                raise ValueError("account_policy.registration_mode must say how accounts are created")
            if mode == "open":
                role = str(policy.get("registration_role") or "").strip()
                if not role:
                    raise ValueError("public sign-up needs one explicit registration_role")
                key = re.sub(r"[^a-z0-9]+", "_", role.lower()).strip("_")
                if any(word in key for word in ("admin", "manager", "owner", "staff", "cashier", "operator", "moderator")):
                    raise ValueError("public sign-up cannot create a privileged role")
            if mode in {"admin_created", "invite", "request"} and not str(policy.get("provisioning_role") or "").strip():
                raise ValueError(f"{mode} account creation needs one explicit provisioning_role")

    return validate


