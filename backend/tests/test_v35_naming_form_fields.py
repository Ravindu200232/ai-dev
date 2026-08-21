"""Every form control gets a name before a test ever looks for one."""
import unittest
from pathlib import Path

from agents.architect_next_rules import (field_label, name_unnamed_fields,
                                         unnamed_fields)

# The admin price cell exactly as the build wrote it.
REAL = """
'use client'
export default function RoomAdminRow({ room }) {
  const [price, setPrice] = useState(room.price)
  const [cleaning, setCleaning] = useState(room.cleaning_status)
  return (
    <tr>
      <td>
        <input
          type="number"
          value={price}
          onChange={(e) => setPrice(parseFloat(e.target.value))}
          className="w-24 p-1 border-2"
        />
      </td>
      <td>
        <input type="checkbox" checked={cleaning} onChange={(e) => setCleaning(e.target.checked)} />
      </td>
      <td><button data-testid="save-room">Save</button></td>
    </tr>
  )
}
"""


class LabelFromBindingTests(unittest.TestCase):
    def test_the_name_comes_from_what_the_box_holds(self):
        self.assertEqual(field_label('value={price} onChange={x}'), "Price")
        self.assertEqual(field_label('checked={cleaning}'), "Cleaning")

    def test_camel_and_snake_names_read_like_english(self):
        self.assertEqual(field_label("value={cleaningStatus}"), "Cleaning status")
        self.assertEqual(field_label("value={guest_name}"), "Guest name")

    def test_a_dotted_binding_uses_the_field_not_the_row(self):
        self.assertEqual(field_label("value={form.emailAddress}"), "Email address")

    def test_a_meaningless_variable_falls_back_to_the_type(self):
        self.assertEqual(field_label('type="date" value={v}'), "Date")
        self.assertEqual(field_label('value={x}'), "")

    def test_the_setter_is_read_when_there_is_no_value(self):
        self.assertEqual(field_label("onChange={(e) => setDiscountCode(e)}"),
                         "Discount code")


class NamingPassTests(unittest.TestCase):
    def test_the_real_admin_row_ends_up_fully_named(self):
        self.assertEqual(len(unnamed_fields({"a.jsx": REAL})), 2)
        fixed, notes = name_unnamed_fields(REAL)
        self.assertEqual(len(notes), 2)
        self.assertEqual(unnamed_fields({"a.jsx": fixed}), [])
        self.assertIn('<input aria-label="Price"', fixed)
        self.assertIn('<input aria-label="Cleaning"', fixed)

    def test_a_control_that_already_has_a_name_is_left_alone(self):
        for attrs in ('name="price"', 'id="price"', 'aria-label="Cost"',
                      'data-testid="price"', 'placeholder="Price"'):
            body = f'<input type="number" {attrs} value={{price}} />'
            self.assertEqual(name_unnamed_fields(body), (body, []), attrs)

    def test_a_hidden_field_is_never_labelled(self):
        body = '<input type="hidden" value={token} />'
        self.assertEqual(name_unnamed_fields(body), (body, []))

    def test_nothing_else_in_the_file_moves(self):
        fixed, _ = name_unnamed_fields(REAL)
        for keep in ("'use client'", "setPrice(parseFloat", 'data-testid="save-room"',
                     'className="w-24 p-1 border-2"'):
            self.assertIn(keep, fixed)
        self.assertEqual(fixed.count("<input"), REAL.count("<input"))

    def test_a_file_with_nothing_to_do_is_returned_untouched(self):
        body = "export default function P(){ return <div/> }"
        self.assertEqual(name_unnamed_fields(body), (body, []))

    def test_selects_and_textareas_are_named_too(self):
        _, notes = name_unnamed_fields(
            "<select value={status}/><textarea value={reviewText}/>")
        self.assertEqual(len(notes), 2)
        self.assertIn("Status", notes[0])
        self.assertIn("Review text", notes[1])


class WiredIntoEveryBuildTests(unittest.TestCase):
    def test_it_runs_with_the_other_mechanical_repairs(self):
        text = Path("agents/architect_boundaries.py").read_text(encoding="utf-8")
        chain = text[text.index("def apply_next_fixes"):]
        self.assertIn("self.name_form_fields()", chain)
        self.assertLess(chain.index("self.name_form_fields()"),
                        chain.index("self.enforce_dynamic()"))

    def test_the_pass_exists_on_the_architect(self):
        from agents.architect import ArchitectAgent
        self.assertTrue(callable(getattr(ArchitectAgent, "name_form_fields", None)))


if __name__ == "__main__":
    unittest.main()
