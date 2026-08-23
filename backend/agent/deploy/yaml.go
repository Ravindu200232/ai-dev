package deploy

import "strings"

// yamlDoc is the two levels of a YAML file this package needs: the top-level
// keys and what is directly under each of them.
//
// It is not a YAML parser and must never be used as one. It reads files this
// agent generated from its own templates, to check they still say what they
// are supposed to — which is why a dependency for the job would be a
// dependency carried for one screenful of string handling.
type yamlDoc struct {
	sections map[string]map[string]string
	order    []string
}

func readYAML(body string) yamlDoc {
	doc := yamlDoc{sections: map[string]map[string]string{}}
	section := ""
	for _, line := range strings.Split(strings.ReplaceAll(body, "\r\n", "\n"), "\n") {
		if strings.TrimSpace(line) == "" || strings.HasPrefix(strings.TrimSpace(line), "#") {
			continue
		}
		switch {
		case !strings.HasPrefix(line, " "):
			key, _, ok := strings.Cut(line, ":")
			if !ok {
				section = ""
				continue
			}
			section = strings.TrimSpace(key)
			if _, seen := doc.sections[section]; !seen {
				doc.sections[section] = map[string]string{}
				doc.order = append(doc.order, section)
			}
		// Exactly one level in. Anything deeper belongs to a job, a step or a
		// block scalar, none of which this needs to understand.
		case section != "" && strings.HasPrefix(line, "  ") && !strings.HasPrefix(line, "   "):
			key, value, ok := strings.Cut(strings.TrimSpace(line), ":")
			if !ok {
				continue
			}
			doc.sections[section][strings.TrimSpace(key)] = strings.TrimSpace(value)
		}
	}
	return doc
}

// hasSection reports whether a top-level key exists and has something under it.
func (d yamlDoc) hasSection(name string) bool {
	entries, ok := d.sections[name]
	return ok && len(entries) > 0
}

// hasKey reports whether a key exists directly under a top-level section.
func (d yamlDoc) hasKey(section, key string) bool {
	_, ok := d.sections[section][key]
	return ok
}

// value is what a key directly under a section is set to, or "".
func (d yamlDoc) value(section, key string) string {
	return d.sections[section][key]
}
