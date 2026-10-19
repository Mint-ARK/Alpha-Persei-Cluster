# -*- coding: utf-8 -*-
"""Persistence helpers for saved formation plans (cs_63010 / sc_63005 f4).

Only the identifying protobuf fields are interpreted here. The nested
``cont_team`` and ``data`` message bodies are kept byte-for-byte so fields
unknown to this server survive a save and login round trip.
"""
import time


class FormationCodecError(ValueError):
    """Malformed or inconsistent formation protobuf data."""


_MAX_FIELD_NUMBER = (1 << 29) - 1
_MAX_UINT32 = (1 << 32) - 1


def _read_varint(data, pos):
    value = 0
    for index in range(10):
        if pos >= len(data):
            raise FormationCodecError("truncated protobuf varint")
        byte = data[pos]
        pos += 1
        if index == 9 and byte > 1:
            raise FormationCodecError("protobuf varint exceeds 64 bits")
        value |= (byte & 0x7f) << (index * 7)
        if byte < 0x80:
            return value, pos
    raise FormationCodecError("unterminated protobuf varint")


def _read_key(data, pos):
    key, pos = _read_varint(data, pos)
    field_number = key >> 3
    wire_type = key & 7
    if field_number < 1 or field_number > _MAX_FIELD_NUMBER:
        raise FormationCodecError("invalid protobuf field number")
    if wire_type > 5:
        raise FormationCodecError("invalid protobuf wire type")
    return field_number, wire_type, pos


def _skip_group(data, pos, group_field):
    while pos < len(data):
        field_number, wire_type, pos = _read_key(data, pos)
        if wire_type == 4:
            if field_number != group_field:
                raise FormationCodecError("mismatched protobuf end-group")
            return pos
        pos = _skip_value(data, pos, field_number, wire_type)
    raise FormationCodecError("unterminated protobuf group")


def _skip_value(data, pos, field_number, wire_type):
    if wire_type == 0:
        _, pos = _read_varint(data, pos)
        return pos
    if wire_type == 1:
        end = pos + 8
    elif wire_type == 2:
        length, pos = _read_varint(data, pos)
        end = pos + length
    elif wire_type == 3:
        return _skip_group(data, pos, field_number)
    elif wire_type == 5:
        end = pos + 4
    else:
        raise FormationCodecError("unexpected protobuf end-group")
    if end > len(data):
        raise FormationCodecError("truncated protobuf field")
    return end


def _iter_fields(data):
    """Yield (field number, wire type, value), validating message framing."""
    data = memoryview(data).cast("B")
    pos = 0
    while pos < len(data):
        field_number, wire_type, pos = _read_key(data, pos)
        if wire_type == 4:
            raise FormationCodecError("unexpected protobuf end-group")
        if wire_type == 0:
            value, pos = _read_varint(data, pos)
        elif wire_type == 1:
            end = pos + 8
            if end > len(data):
                raise FormationCodecError("truncated protobuf fixed64 field")
            value = None
            pos = end
        elif wire_type == 2:
            length, pos = _read_varint(data, pos)
            end = pos + length
            if end > len(data):
                raise FormationCodecError("truncated protobuf message field")
            value = bytes(data[pos:end])
            pos = end
        elif wire_type == 3:
            value = None
            pos = _skip_group(data, pos, field_number)
        else:  # wire_type == 5
            end = pos + 4
            if end > len(data):
                raise FormationCodecError("truncated protobuf fixed32 field")
            value = None
            pos = end
        yield field_number, wire_type, value


def _uint32(value, label):
    if not isinstance(value, int) or isinstance(value, bool):
        raise FormationCodecError("%s must be an integer" % label)
    if value < 0 or value > _MAX_UINT32:
        raise FormationCodecError("%s is outside uint32 range" % label)
    return value


def _required_varint(body, wanted_field, label):
    found = False
    value = None
    for field_number, wire_type, field_value in _iter_fields(body):
        if field_number != wanted_field:
            continue
        if wire_type != 0:
            raise FormationCodecError("%s has the wrong wire type" % label)
        found = True
        value = _uint32(field_value, label)
    if not found:
        raise FormationCodecError("missing required %s" % label)
    return value


def _required_length_field(body, wanted_field, label):
    found = False
    for field_number, wire_type, _ in _iter_fields(body):
        if field_number != wanted_field:
            continue
        if wire_type != 2:
            raise FormationCodecError("%s has the wrong wire type" % label)
        found = True
    if not found:
        raise FormationCodecError("missing required %s" % label)


def _validate_cont_team(cont_team):
    return _required_varint(cont_team, 1, "cont_team.cont_id")


def _validate_data(data_rec):
    cont_id = _required_varint(data_rec, 1, "data.cont_id")
    _required_length_field(data_rec, 2, "data.name")
    return cont_id


def _payload_bytes(payload):
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise FormationCodecError("cs_63010 payload must be bytes-like")
    return bytes(payload)


def _parse_cs_63010(payload):
    payload = _payload_bytes(payload)
    team_type = None
    cont_team_parts = []
    data_parts = []
    for field_number, wire_type, value in _iter_fields(payload):
        if field_number == 1:
            if wire_type != 0:
                raise FormationCodecError("team_type has the wrong wire type")
            team_type = _uint32(value, "team_type")
        elif field_number in (2, 3):
            if wire_type != 2:
                raise FormationCodecError("formation message field has the wrong wire type")
            if field_number == 2:
                cont_team_parts.append(value)
            else:
                data_parts.append(value)

    if team_type is None:
        raise FormationCodecError("missing required team_type")
    if not cont_team_parts:
        raise FormationCodecError("missing required cont_team")
    if not data_parts:
        raise FormationCodecError("missing required data")

    # A singular protobuf message may appear multiple times on the wire; its
    # merge semantics concatenate the encoded submessage fields.
    cont_team = b"".join(cont_team_parts)
    data_rec = b"".join(data_parts)
    cont_id = _validate_cont_team(cont_team)
    data_cont_id = _validate_data(data_rec)
    if cont_id != data_cont_id:
        raise FormationCodecError("cont_team and data cont_id do not match")
    return team_type, cont_id, cont_team, data_rec


def _sql_int(value, label):
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise FormationCodecError("%s must be an integer" % label) from exc
    if number < 0 or number > (1 << 63) - 1:
        raise FormationCodecError("%s is outside SQLite integer range" % label)
    return number


def save_formation(db, uid, payload):
    """Validate and atomically upsert one cs_63010 formation plan.

    Returns ``True`` only after the transaction context exits successfully.
    Database exceptions are intentionally allowed to propagate.
    """
    if db is None or not callable(getattr(db, "transaction", None)):
        raise FormationCodecError("db must provide transaction()")
    if not callable(getattr(db, "execute", None)):
        raise FormationCodecError("db must provide execute()")

    team_type, cont_id, cont_team, data_rec = _parse_cs_63010(payload)
    uid = _sql_int(uid, "uid")
    update_ts = int(time.time())
    with db.transaction():
        result = db.execute(
            "INSERT INTO formation_team "
            "(uid, team_type, cont_id, cont_team, data_rec, update_ts) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid, team_type, cont_id) DO UPDATE SET "
            "cont_team=excluded.cont_team, data_rec=excluded.data_rec, "
            "update_ts=excluded.update_ts",
            (uid, team_type, cont_id, cont_team, data_rec, update_ts),
        )
        rowcount = getattr(result, "rowcount", None)
        if rowcount is not None and rowcount != 1:
            raise FormationCodecError("formation upsert did not affect one row")
    return True


def _encode_varint(value):
    value = int(value)
    output = bytearray()
    while value > 0x7f:
        output.append((value & 0x7f) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def _pb_varint(field_number, value):
    return _encode_varint((field_number << 3) | 0) + _encode_varint(value)


def _pb_message(field_number, body):
    return (_encode_varint((field_number << 3) | 2) +
            _encode_varint(len(body)) + body)


def _query_rows(db, sql, args):
    query = getattr(db, "query", None)
    if callable(query):
        return query(sql, args)
    execute = getattr(db, "execute", None)
    if not callable(execute):
        raise FormationCodecError("db must provide query() or execute()")
    return [dict(row) for row in execute(sql, args).fetchall()]


def _stored_blob(value, label):
    if value is None:
        raise FormationCodecError("stored %s is null" % label)
    try:
        return bytes(value)
    except (TypeError, ValueError) as exc:
        raise FormationCodecError("stored %s is not bytes-like" % label) from exc


def build_formation_fields(db, uid):
    """Build the repeated sc_63005 field-4 bytes for a user's saved plans."""
    if db is None:
        return b""
    uid = _sql_int(uid, "uid")
    rows = _query_rows(
        db,
        "SELECT team_type, cont_id, cont_team, data_rec "
        "FROM formation_team WHERE uid=? ORDER BY team_type, cont_id",
        (uid,),
    )
    grouped = {}
    for row in rows:
        team_type = _uint32(int(row["team_type"]), "stored team_type")
        cont_id = _uint32(int(row["cont_id"]), "stored cont_id")
        cont_team = _stored_blob(row["cont_team"], "cont_team")
        data_rec = _stored_blob(row["data_rec"], "data_rec")
        if _validate_cont_team(cont_team) != cont_id:
            raise FormationCodecError("stored cont_team cont_id does not match row")
        if _validate_data(data_rec) != cont_id:
            raise FormationCodecError("stored data cont_id does not match row")
        grouped.setdefault(team_type, []).append((cont_team, data_rec))

    output = bytearray()
    for team_type in sorted(grouped):
        formation = bytearray(_pb_varint(1, team_type))
        for cont_team, data_rec in grouped[team_type]:
            formation.extend(_pb_message(2, cont_team))
            formation.extend(_pb_message(3, data_rec))
        output.extend(_pb_message(4, bytes(formation)))
    return bytes(output)
