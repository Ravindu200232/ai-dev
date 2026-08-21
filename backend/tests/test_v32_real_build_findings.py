"""The three defects behind `nothing matched textbox role /price/i`."""
import unittest

from agents.architect_next_rules import unnamed_fields
from qa_agent.e2e_common import role_of, same_role_family

# The admin price cell exactly as a real build wrote it.
ROOM_ADMIN_ROW = """
'use client'
export default function RoomAdminRow({ room }) {
  return (
    <tr>
      <td>
        {isEditing ? (
          <input
            type="number"
            value={price}
            onChange={(e) => setPrice(parseFloat(e.target.value))}
            className="w-24 p-1 border-2"
          />
        ) : (
          <span>${room.price}</span>
        )}
      </td>
      <td>
        <input type="checkbox" checked={cleaning} onChange={(e) => x(e)} />
      </td>
      <td>
        <button onClick={handleSave} data-testid="save-room">Save</button>
      </td>
    </tr>
  )
}
"""

FIXED = ROOM_ADMIN_ROW.replace(
    '<input\n            type="number"',
    '<input\n            aria-label="Price"\n            type="number"')


class InputRoleTests(unittest.TestCase):
    def test_a_number_box_is_a_spinbutton_not_a_textbox(self):
        self.assertEqual(role_of("input", "number"), "spinbutton")
        self.assertEqual(role_of("input", "text"), "textbox")
        self.assertEqual(role_of("input", "search"), "searchbox")
        self.assertEqual(role_of("input", "checkbox"), "checkbox")

    def test_the_other_tags_map_the_way_playwright_sees_them(self):
        self.assertEqual(role_of("textarea"), "textbox")
        self.assertEqual(role_of("select"), "combobox")
        self.assertEqual(role_of("button"), "button")
        self.assertEqual(role_of("div"), "")

    def test_a_scenario_asking_for_textbox_still_matches_a_number_box(self):
        self.assertTrue(same_role_family("textbox", "spinbutton"))
        self.assertTrue(same_role_family("textbox", "searchbox"))
        self.assertTrue(same_role_family("combobox", "textbox"))

    def test_it_does_not_match_across_kinds_of_control(self):
        self.assertFalse(same_role_family("textbox", "button"))
        self.assertFalse(same_role_family("checkbox", "spinbutton"))
        self.assertFalse(same_role_family("", "textbox"))


class UnnamedFieldTests(unittest.TestCase):
    def test_the_price_box_from_the_real_build_is_reported(self):
        found = unnamed_fields({"components/RoomAdminRow.jsx": ROOM_ADMIN_ROW})
        self.assertEqual(len(found), 2)
        self.assertTrue(all(f["tag"] == "input" for f in found))
        self.assertIn('type="number"', found[0]["snippet"])

    def test_adding_an_aria_label_clears_it(self):
        found = unnamed_fields({"components/RoomAdminRow.jsx": FIXED})
        self.assertEqual([f["snippet"] for f in found if "number" in f["snippet"]], [])

    def test_any_one_way_of_naming_a_field_is_enough(self):
        for attrs in ('name="price"', 'id="price"', 'placeholder="Price"',
                      'data-testid="price"', 'aria-label="Price"'):
            body = f'<input type="number" {attrs} />'
            self.assertEqual(unnamed_fields({"a.jsx": body}), [], attrs)

    def test_a_hidden_field_is_not_something_a_person_fills(self):
        self.assertEqual(unnamed_fields({"a.jsx": '<input type="hidden" value={x} />'}), [])

    def test_selects_and_textareas_are_held_to_the_same_rule(self):
        found = unnamed_fields({"a.jsx": "<select value={v}><option/></select>"
                                         "<textarea value={t} />"})
        self.assertEqual({f["tag"] for f in found}, {"select", "textarea"})

    def test_it_only_reads_component_files(self):
        self.assertEqual(unnamed_fields({"README.md": "<input />"}), [])

    def test_the_builder_is_told_about_the_measured_failure(self):
        from pathlib import Path
        text = Path("agents/architect_next_builder_prompt_a.py").read_text(encoding="utf-8")
        self.assertIn("spinbutton", text)
        self.assertIn("data-testid`", text)


if __name__ == "__main__":
    unittest.main()
