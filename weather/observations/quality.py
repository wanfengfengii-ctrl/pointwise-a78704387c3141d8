from weather.observations.models import ObservationError, Quality, QualityState


def decode_quality_state(word, width):
    if type(word) is not int or not 0 <= word < 1 << width:
        raise ObservationError("quality word outside width")
    if word == (1 << width) - 1:
        return Quality(QualityState.MISSING)
    return Quality(QualityState.VALUE, word)


def resolve_group_quality(group, members, block):
    if any(member.occurrence.group != group for member in members):
        raise ObservationError("quality member outside group")
    identities = [(m.occurrence.template_path, m.occurrence.occurrence_path) for m in members]
    if len(set(identities)) != len(identities):
        raise ObservationError("duplicate group member")
    if block is None:
        return tuple(Quality(QualityState.UNSELECTED) for _ in members)
    if block.group != group or len(block.bitmap) != len(members):
        raise ObservationError("quality bitmap outside group reference domain")
    if any(type(selected) is not bool for selected in block.bitmap):
        raise ObservationError("invalid bitmap member")
    if sum(block.bitmap) != len(block.words):
        raise ObservationError("quality word count mismatch")
    words = iter(block.words)
    return tuple(decode_quality_state(next(words), block.width) if selected
                 else Quality(QualityState.UNSELECTED) for selected in block.bitmap)
