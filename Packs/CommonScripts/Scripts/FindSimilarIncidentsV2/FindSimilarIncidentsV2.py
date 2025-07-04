from CommonServerPython import *
from dateutil import parser  # type: ignore[import]

EXACT_MATCH = 0
CONTAINS = '*'

SEVERITY_MAP = {
    '0': 'Unknown',
    '0.5': 'Informational',
    '1': 'Low',
    '2': 'Medium',
    '3': 'High',
    '4': 'Critical'
}

STATUS_MAP = {
    '0': 'Pending',
    '1': 'Active',
    '2': 'Closed',
    '3': 'Closed'
}


def get_incident_alias():
    if is_xsiam():
        return 'alert'
    elif is_platform():
        return 'issue'
    else:
        return 'incident'


INCIDENT_ALIAS = get_incident_alias()


def parse_input(csv):
    if not csv:
        return {}
    values = csv.split(",")
    keys = []
    equality_map = {}
    for value in values:
        if ':' in value:
            value, count = value.split(':')
            value = value.strip()
            equality_map[value] = int(count.strip()) if count != CONTAINS else count
        else:
            equality_map[value] = EXACT_MATCH
        keys.append(value.strip())
    return equality_map


def remove_prefix(prefix, s):
    if s and s.startswith(prefix):
        return s[len(prefix):]
    return s


def parse_datetime(datetime_str):
    return parser.parse(datetime_str)


def nested_dict_flatted(d, parent_key='', sep='.'):
    if d:
        items = []  # type: ignore
        for k, v in list(d.items()):
            new_key = parent_key + sep + k if parent_key else k
            if isinstance(v, list) and len(v) > 0:
                v = v[0]
            if isinstance(v, dict) and len(v) > 0:
                items.extend(list(nested_dict_flatted(v, new_key, sep=sep).items()))
            else:
                items.append((new_key, v))
        return dict(items)
    else:
        return {}


def get_map_from_nested_dict(nested_dict, keys, raise_error=False):
    result = {}
    flat_dict = nested_dict_flatted(nested_dict)
    for key in keys:
        if key:
            if key in flat_dict:
                result[key] = flat_dict[key]
            elif key in nested_dict:
                result[key] = nested_dict[key]
            else:
                message = f"Missing key\label\custom\context field for {INCIDENT_ALIAS}: {key}"
                if raise_error:
                    return_error(message)
                else:
                    continue
    return result


def get_incident_labels_map(labels):
    if labels is None:
        return {}
    labels_map = {}  # type: ignore
    for label in labels:
        label_type = label['type']
        label_value = label['value']
        if label_type in labels_map:
            if not isinstance(labels_map[label_type], list):
                labels_map[label_type] = [labels_map[label_type]]
            labels_map[label_type].append(label_value)
        else:
            labels_map[label_type] = label_value

    for label_key, label_value in list(labels_map.items()):
        if isinstance(label_value, list):
            label_value.sort()
            labels_map[label_key] = label_value

    return labels_map


def handle_str_field(key, value):
    value = value.replace('"', r'\"').replace("\n", "\\n").replace("\r", "\\r").replace(r'\\"', r'\\\"')
    query = f'{key}="{value}"'
    return query


def handle_int_field(key, value):
    query_template = '{}:={}'
    return query_template.format(key, str(value))


def handle_list_field(key, value):
    if not value:  # empty list
        return '{}={}'.format(key, str(value))
    queries_list = []
    for item in value:
        queries_list.append(handle_field[type(item)](key, item))
    return queries_list


handle_field = {
    int: handle_int_field,
    str: handle_str_field,
    list: handle_list_field
}


def build_incident_fields_query(incident_data):
    similar_keys_list = []
    for key, value in list(incident_data.items()):
        result = handle_field[type(value)](key, value)
        similar_keys_list.extend(result) if isinstance(result, list) else \
            similar_keys_list.append(result)  # type: ignore

    return similar_keys_list


def get_incidents_by_keys(similar_incident_keys, time_field, incident_time, incident_id, hours_back, ignore_closed,
                          max_number_of_results, extra_query, applied_condition):
    condition_string = ' %s ' % applied_condition.lower()

    incident_fields_query = build_incident_fields_query(similar_incident_keys)
    similar_keys_query = condition_string.join(incident_fields_query)
    incident_time = parse_datetime(incident_time)
    max_date = incident_time
    min_date = incident_time - timedelta(hours=hours_back)
    query = build_incident_query(similar_keys_query, ignore_closed, incident_id, extra_query)

    demisto.debug(f"Find similar {INCIDENT_ALIAS}s based on initial query: {query}")

    get_incidents_argument = {'query': query, 'size': max_number_of_results, 'sort': '%s.desc' % time_field}

    get_incidents_argument['fromdate'] = min_date.isoformat()
    get_incidents_argument['todate'] = max_date.isoformat()

    res = demisto.executeCommand("getIncidents", get_incidents_argument)
    if res[0]['Type'] == entryTypes['error']:
        return_error(str(res[0]['Contents']))

    incident_list = res[0]['Contents']['data'] or []
    return incident_list


def get_context(incident_id):
    res = demisto.executeCommand("getContext", {'id': incident_id})
    try:
        return res[0]['Contents']['context']
    except Exception:
        return {}


def camel_case_to_space(s):
    return ''.join([x if x.islower() else " " + x for x in s]).strip().capitalize()


def incident_to_record(incident, time_field):
    def parse_time(date_time_str):
        try:

            if date_time_str.find('.') > 0:
                date_time_str = date_time_str[:date_time_str.find('.')]
            if date_time_str.find('+') > 0:
                date_time_str = date_time_str[:date_time_str.find('+')]
            return date_time_str.replace('T', ' ')
        except Exception:
            return date_time_str

    time = parse_time(incident[time_field])
    return {'id': "[%s](#/Details/%s)" % (incident['id'], incident['id']),
            'rawId': incident['id'],
            'name': incident['name'],
            'closedTime': parse_time(incident['closed']) if incident['closed'] != "0001-01-01T00:00:00Z" else "",
            'time': time}


def is_text_equal_by_x_different_words(text1, text2, number_of_different_words, separator=' '):
    if not isinstance(text1, str):
        text1 = str(text1)
    if not isinstance(text2, str):
        text2 = str(text2)
    text1 = text1.lower()
    text2 = text2.lower()
    if number_of_different_words == EXACT_MATCH:
        return text1 == text2
    elif number_of_different_words == CONTAINS:
        return text1.find(text2) >= 0 or text2.find(text1) >= 0
    else:
        words_set1 = set([x for x in [x.strip() for x in text1.replace("\\n", separator).split(separator)] if x])
        words_set2 = set([x for x in [x.strip() for x in text2.replace("\\n", separator).split(separator)] if x])
        return len(words_set1.difference(words_set2)) <= number_of_different_words and len(
            words_set2.difference(words_set1)) <= number_of_different_words


def verify_map_equals(values_map1, values_map2, equality_map):
    if not equality_map or len(equality_map) == 0:
        return True

    if not values_map1 or len(values_map1) == 0 or not values_map2 or len(values_map2) == 0:
        return False

    for key in equality_map:
        if key not in values_map1 or key not in values_map2:
            return False

        value1 = values_map1[key]
        value2 = values_map2[key]

        if isinstance(value1, str) and isinstance(value2, str):
            is_values_equals = is_text_equal_by_x_different_words(values_map1[key], values_map2[key], equality_map[key])

            if not is_values_equals:
                return False

        elif isinstance(value1, list) and isinstance(value2, list):
            try:
                return set(value1) == set(value2)
            except Exception:
                return value1 == value2
        else:

            return value1 == value2
    return True


def did_not_found_duplicates():
    context = {
        'isSimilarIncidentFound': False
    }
    demisto.results({'ContentsFormat': formats['markdown'],
                     'Type': entryTypes['note'],
                     'Contents': f'No duplicate {INCIDENT_ALIAS}s has been found.',
                     'EntryContext': context})
    sys.exit(0)


def merge_incident_fields(incident):
    custom_fields = incident.get('CustomFields', {}) or {}
    for k, v in list(custom_fields.items()):
        incident[k] = v
    incident['severity'] = SEVERITY_MAP.get(str(incident['severity']))
    incident['status'] = STATUS_MAP.get(str(incident['status']))
    return incident


def build_incident_query(similar_keys_query, ignore_closed, incident_id, extra_query):
    query = ''

    if similar_keys_query:
        query = similar_keys_query

    if ignore_closed:
        query += " and -status:Closed" if query else "-status:Closed"

    if incident_id:
        query = "(-id:%s) and (%s)" % (incident_id, query) if query else "(-id:%s)" % (incident_id)

    if extra_query:
        query += " and (%s)" % extra_query if query else extra_query
    return query


def main():
    SIMILAR_INCIDENT_KEYS = [x for x in demisto.args().get('similarIncidentKeys', '').split(",") if x]
    SIMILAR_CUSTOMS_FIELDS_MAP = parse_input(demisto.args().get('similarCustomFields', ''))
    SIMILAR_INCIDENTS_FIELDS_MAP = parse_input(demisto.args().get('similarIncidentFields', ''))
    if len(SIMILAR_INCIDENTS_FIELDS_MAP) > 0 and (
            len(SIMILAR_INCIDENT_KEYS) > 0 or len(SIMILAR_CUSTOMS_FIELDS_MAP) > 0):
        return_error('If using similarIncidentFields do not use deprecated similarCustomFields\\similarIncidentKeys')
    else:
        SIMILAR_INCIDENTS_FIELDS_MAP.update(SIMILAR_CUSTOMS_FIELDS_MAP)
        for k in [remove_prefix('incident.', x) for x in SIMILAR_INCIDENT_KEYS]:
            if k and len(k) > 0:
                SIMILAR_INCIDENTS_FIELDS_MAP[k] = EXACT_MATCH

    SIMILAR_LABELS_MAP = parse_input(demisto.args().get('similarLabelsKeys', ''))
    SIMILAR_CONTEXT_MAP = parse_input(demisto.args().get('similarContextKeys', ''))
    HOURS_BACK = float(demisto.args()['hoursBack'])
    TIME_FIELD = demisto.args()['timeField']
    IGNORE_CLOSED = demisto.args()['ignoreClosedIncidents'] == 'yes'
    MAX_NUMBER_OF_INCIDENTS = int(demisto.args()['maxNumberOfIncidents'])
    MAX_CANDIDATES_IN_LIST = int(demisto.args()['maxResults'])
    EXTRA_QUERY = demisto.args().get('filterQuery')
    INCIDENT_FIELDS_APPLIED_CONDITION = demisto.args()['incidentFieldsAppliedCondition']
    RAISE_ERROR_MISSING_VALUES = not (demisto.args()['skipMissingValues'] == 'yes')

    # set the incident
    incident = merge_incident_fields(demisto.incidents()[0])  # type: ignore  # pylint: disable=no-value-for-parameter

    # validate fields
    exact_match_incident_fields = get_map_from_nested_dict(incident,
                                                           {k: v for k, v in list(SIMILAR_INCIDENTS_FIELDS_MAP.items())
                                                            if
                                                            v == EXACT_MATCH}, raise_error=RAISE_ERROR_MISSING_VALUES)
    SIMILAR_INCIDENTS_FIELDS_MAP = {k: v for k, v in list(SIMILAR_INCIDENTS_FIELDS_MAP.items()) if v != EXACT_MATCH}
    similar_incident_fields = get_map_from_nested_dict(incident, list(SIMILAR_INCIDENTS_FIELDS_MAP.keys()),
                                                       raise_error=RAISE_ERROR_MISSING_VALUES)
    labels_map = get_incident_labels_map(incident.get('labels', []))
    incident_similar_labels = get_map_from_nested_dict(labels_map, list(SIMILAR_LABELS_MAP.keys()),
                                                       raise_error=RAISE_ERROR_MISSING_VALUES)
    incident_similar_context = demisto.context()
    original_context_map = {}

    if incident_similar_context:
        for key in list(SIMILAR_CONTEXT_MAP.keys()):
            response = demisto.dt(incident_similar_context, key)
            original_context_map[key] = response
            if not response and RAISE_ERROR_MISSING_VALUES:
                raise ValueError(f"Error: Missing context key for {INCIDENT_ALIAS}: {key}")

    log_message = f'{INCIDENT_ALIAS.capitalize()} fields with exact match: %s' % exact_match_incident_fields
    if len(exact_match_incident_fields) > 1:
        log_message += ', applied with %s condition' % INCIDENT_FIELDS_APPLIED_CONDITION
    demisto.debug(log_message)
    if len(similar_incident_fields) > 0:
        demisto.debug(f'Similar {INCIDENT_ALIAS} fields (not exact match): {similar_incident_fields}')
    if len(incident_similar_labels) > 0:
        demisto.debug('Similar labels: %s' % incident_similar_labels)
    if len(incident_similar_context) > 0:
        demisto.debug('Similar context keys: %s' % original_context_map)

    if len(exact_match_incident_fields) == 0 and len(similar_incident_fields) == 0 and len(
            incident_similar_labels) == 0 and len(original_context_map) == 0:
        return_error(f"Does not have any field to compare in the current {INCIDENT_ALIAS}")

    duplicate_incidents = get_incidents_by_keys(exact_match_incident_fields, TIME_FIELD, incident[TIME_FIELD],
                                                incident['id'],
                                                HOURS_BACK, IGNORE_CLOSED, MAX_NUMBER_OF_INCIDENTS, EXTRA_QUERY,
                                                INCIDENT_FIELDS_APPLIED_CONDITION)

    if len(duplicate_incidents) == 0:
        did_not_found_duplicates()
    duplicate_incidents = list(map(merge_incident_fields, duplicate_incidents))

    # filter by labels
    if len(incident_similar_labels or {}) > 0:
        duplicate_incidents = [c for c in duplicate_incidents if
                               verify_map_equals(incident_similar_labels,
                                                 get_incident_labels_map(c.get('labels', [])),
                                                 SIMILAR_LABELS_MAP)

                               ]

    # filter by incident similar fields
    if len(similar_incident_fields or {}) > 0:
        duplicate_incidents = [c for c in duplicate_incidents
                               if verify_map_equals(similar_incident_fields,
                                                    get_map_from_nested_dict(c,
                                                                             list(SIMILAR_INCIDENTS_FIELDS_MAP.keys()),
                                                                             raise_error=False),
                                                    SIMILAR_INCIDENTS_FIELDS_MAP)
                               ]
    filter_by_context = []
    if original_context_map:
        for c in duplicate_incidents:
            other_incident_context = get_context(c['id'])

            if other_incident_context:
                other_incident_context_map = {}

                for key in list(SIMILAR_CONTEXT_MAP.keys()):
                    response = demisto.dt(other_incident_context, key)
                    if response:
                        other_incident_context_map[key] = response
                        if verify_map_equals(original_context_map, other_incident_context_map, SIMILAR_CONTEXT_MAP):
                            filter_by_context.append(c)
        duplicate_incidents = filter_by_context

    # update context
    if len(duplicate_incidents or []) > 0:
        duplicate_incidents_rows = [incident_to_record(x, TIME_FIELD) for x in duplicate_incidents]

        duplicate_incidents_rows = list(sorted(duplicate_incidents_rows, key=lambda x: (x['time'], x['id'])))

        context = {
            'similarIncidentList': duplicate_incidents_rows[:MAX_CANDIDATES_IN_LIST],
            'similarIncident': duplicate_incidents_rows[0],
            'isSimilarIncidentFound': True
        }

        duplicate_incidents_rows = duplicate_incidents_rows[:MAX_CANDIDATES_IN_LIST]
        hr_result = [dict((camel_case_to_space(k), v) for k, v in list(row.items())) for row in
                     duplicate_incidents_rows]
        markdown_result = tableToMarkdown(f"Duplicate {INCIDENT_ALIAS}s",
                                          hr_result,
                                          headers=['Id', 'Name', 'Closed time', 'Time'])
        return {'ContentsFormat': formats['markdown'],
                'Type': entryTypes['note'],
                'Contents': markdown_result,
                'EntryContext': context}
    else:
        did_not_found_duplicates()


if __name__ in ['__main__', '__builtin__', 'builtins']:
    entry = main()
    demisto.results(entry)
