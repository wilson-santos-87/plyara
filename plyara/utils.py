"""plyara utility functions.

This module contains various utility functions for working with plyara output.
"""
import hashlib
import logging
import re

from .core import Parser

# Initialize the logger
logger = logging.getLogger(__name__)


def is_valid_rule_name(entry):
    """Check to see if entry is a valid rule name."""
    # Check if entry is blank
    if not entry:
        return False

    # Check length
    if len(entry) > 128:
        return False

    # Ensure doesn't start with a digit
    if entry[0].isdigit():
        return False

    # Accept only alphanumeric and underscores
    if not re.match(r'\w+$', entry):
        return False

    # Verify not in keywords
    if entry in Parser.KEYWORDS:
        return False

    return True


def is_valid_rule_tag(entry):
    """Check to see if entry is a valid rule tag."""
    # Same lexical conventions as name
    return is_valid_rule_name(entry)


def detect_imports(rule):
    """Take a parsed yararule and provide a list of required imports based on condition."""
    detected_imports = []
    condition_terms = rule['condition_terms']

    for imp in Parser.IMPORT_OPTIONS:
        imp_module = u"{}.".format(imp)

        if imp in condition_terms and imp not in detected_imports:
            detected_imports.append(imp)

        elif imp not in detected_imports:
            for term in condition_terms:
                if term.startswith(imp_module):
                    detected_imports.append(imp)
                    break

    return detected_imports


def detect_dependencies(rule):
    """Take a parsed yararule and provide a list of external rule dependencies."""
    dependencies = []
    string_iteration_variables = []
    condition_terms = rule['condition_terms']

    # Number of terms for index iteration and reference
    term_count = len(condition_terms)

    for index in range(0, term_count):
        # Grab term by index
        term = condition_terms[index]

        if is_valid_rule_name(term) and (term not in Parser.IMPORT_OPTIONS):
            # Grab reference to previous term for logic checks
            if index > 0:
                previous_term = condition_terms[index - 1]
            else:
                previous_term = None

            # Grab reference to next term for logic checks
            if index < (term_count - 1):
                next_term = condition_terms[index + 1]
            else:
                next_term = None

            # Extend term indexes beyond wrapping parentheses for logic checks
            if previous_term == '(' and next_term == ')':
                if (index - 2) >= 0:
                    previous_term = condition_terms[index - 2]
                else:
                    previous_term = None

                if (index + 2) < term_count:
                    next_term = condition_terms[index + 2]
                else:
                    next_term = None

            # Check if reference is a variable for string iteration
            if term in string_iteration_variables:
                continue

            if previous_term in ('any', 'all') and next_term == 'in':
                string_iteration_variables.append(term)
                continue

            # Check for external string variable dependency
            if ((next_term in ('matches', 'contains')) or (previous_term in ('matches', 'contains'))):
                continue

            # Check for external integer variable dependency
            if ((next_term in Parser.COMPARISON_OPERATORS) or (previous_term in Parser.COMPARISON_OPERATORS)):
                continue

            # Check for external boolean dependency may not be possible without stripping out valid rule references

            # Checks for likely rule reference
            if previous_term is None and next_term is None:
                dependencies.append(term)
            elif previous_term in ('and', 'or') or next_term in ('and', 'or'):
                dependencies.append(term)

    return dependencies


def generate_logic_hash(rule):
    """Calculate hash value of rule strings and condition."""
    strings = rule.get('strings', [])
    conditions = rule['condition_terms']

    string_values = []
    condition_mapping = []
    string_mapping = {'anonymous': [], 'named': {}}

    for entry in strings:
        name = entry['name']
        modifiers = entry.get('modifiers', [])

        # Handle string modifiers
        if modifiers:
            value = entry['value'] + u'<MODIFIED>' + u' & '.join(sorted(modifiers))
        else:
            value = entry['value']

        if name == '$':
            # Track anonymous strings
            string_mapping['anonymous'].append(value)
        else:
            # Track named strings
            string_mapping['named'][name] = value

        # Track all string values
        string_values.append(value)

    # Sort all string values
    sorted_string_values = sorted(string_values)

    for condition in conditions:
        # All string references (sort for consistency)
        if condition == 'them' or condition == '$*':
            condition_mapping.append(u'<STRINGVALUE>' + u' | '.join(sorted_string_values))

        elif condition.startswith('$') and condition != '$':
            # Exact Match
            if condition in string_mapping['named']:
                condition_mapping.append(u'<STRINGVALUE>' + string_mapping['named'][condition])
            # Wildcard Match
            elif '*' in condition:
                wildcard_strings = []
                condition = condition.replace('$', r'\$').replace('*', '.*')
                pattern = re.compile(condition)

                for name, value in string_mapping['named'].items():
                    if pattern.match(name):
                        wildcard_strings.append(value)

                wildcard_strings.sort()
                condition_mapping.append(u'<STRINGVALUE>' + u' | '.join(wildcard_strings))
            else:
                logger.error(u'[!] Unhandled String Condition {}'.format(condition))

        # Count Match
        elif condition.startswith('#') and condition != '#':
            condition = condition.replace('#', '$')

            if condition in string_mapping['named']:
                condition_mapping.append('<COUNTOFSTRING>' + string_mapping['named'][condition])
            else:
                logger.error(u'[!] Unhandled String Count Condition {}'.format(condition))

        else:
            condition_mapping.append(condition)

    logic_hash = hashlib.sha1(u''.join(condition_mapping).encode()).hexdigest()
    return logic_hash


def rebuild_yara_rule(rule):
    """Take a parsed yararule and rebuild it into a usable one."""
    rule_format = u"{imports}{scopes}rule {rulename}{tags} {{\n{meta}{strings}{condition}\n}}\n"

    rule_name = rule['rule_name']

    # Rule Imports
    if rule.get('imports'):
        unpacked_imports = [u'import "{}"\n'.format(entry) for entry in rule['imports']]
        rule_imports = u'{}\n'.format(u''.join(unpacked_imports))
    else:
        rule_imports = u''

    # Rule Scopes
    if rule.get('scopes'):
        rule_scopes = u'{} '.format(u' '.join(rule['scopes']))
    else:
        rule_scopes = u''

    # Rule Tags
    if rule.get('tags'):
        rule_tags = u' : {}'.format(u' '.join(rule['tags']))
    else:
        rule_tags = u''

    # Rule Metadata
    if rule.get('metadata'):
        unpacked_meta = []
        kv_list = [(k,) + (v, ) for dic in rule['metadata'] for k, v in dic.items()]

        # Check for and handle correctly quoting string metadata
        for k, v in kv_list:
            if isinstance(v, bool):
                v = str(v).lower()
            elif isinstance(v, int):
                v = str(v)
            else:
                v = '"{}"'.format(v)
            unpacked_meta.append(u'\n\t\t{key} = {value}'.format(key=k, value=v))
        rule_meta = u'\n\tmeta:{}\n'.format(u''.join(unpacked_meta))
    else:
        rule_meta = u''

    # Rule Strings
    if rule.get('strings'):

        string_container = []

        for rule_string in rule['strings']:

            if 'modifiers' in rule_string:
                string_modifiers = u' '.join(rule_string['modifiers'])

                fstring = u'\n\t\t{} = {} {}'.format(rule_string['name'],
                                                     rule_string['value'],
                                                     string_modifiers)
            else:
                fstring = u'\n\t\t{} = {}'.format(rule_string['name'],
                                                  rule_string['value'])

            string_container.append(fstring)

        rule_strings = u'\n\tstrings:{}\n'.format(u''.join(string_container))
    else:
        rule_strings = u''

    if rule.get('condition_terms'):
        # Format condition with appropriate whitespace between keywords
        cond = []

        for term in rule['condition_terms']:

            if not cond:

                if term in Parser.FUNCTION_KEYWORDS:
                    cond.append(term)

                elif term in Parser.KEYWORDS:
                    cond.append(term)
                    cond.append(u' ')

                else:
                    cond.append(term)

            else:

                if cond[-1] == ' ' and term in Parser.FUNCTION_KEYWORDS:
                    cond.append(term)

                elif cond and cond[-1] != ' ' and term in Parser.FUNCTION_KEYWORDS:
                    cond.append(u' ')
                    cond.append(term)

                elif cond[-1] == ' ' and term in Parser.KEYWORDS:
                    cond.append(term)
                    cond.append(u' ')

                elif cond and cond[-1] != ' ' and term in Parser.KEYWORDS:
                    cond.append(u' ')
                    cond.append(term)
                    cond.append(u' ')

                else:
                    cond.append(term)

        fcondition = u''.join(cond).rstrip(' ')
        rule_condition = u'\n\tcondition:\n\t\t{}'.format(fcondition)
    else:
        rule_condition = u''

    formatted_rule = rule_format.format(imports=rule_imports,
                                        rulename=rule_name,
                                        tags=rule_tags,
                                        meta=rule_meta,
                                        scopes=rule_scopes,
                                        strings=rule_strings,
                                        condition=rule_condition)

    return formatted_rule