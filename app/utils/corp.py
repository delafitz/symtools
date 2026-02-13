CORP_LIST = [
    'inc',
    'corp',
    'ltd',
    'plc',
    'na',
    'nv',
    'compan',
    'inter',
    'group',
    'equities',
    'limited',
    'public',
    'holding',
]


def strip_name(raw):
    def check_corp(token):
        if token.lower() == 'co':
            return True
        for tag in CORP_LIST:
            if token.lower().find(tag) == 0:
                return True
        return False

    tokens = (
        raw.replace(',', '')
        .replace('.com', '')
        .replace('.', '')
        .split(' ')
    )

    if len(tokens) == 2 and tokens[1].lower().find('corp') == 0:
        tokens[1] = 'Corp'
        return ' '.join(tokens)

    if tokens[0].lower() == 'the':
        tokens.pop(0)
    name = [tokens.pop(0)]
    for token in tokens[:4]:
        if check_corp(token):
            break
        name.append(token)
    if len(name) > 1:
        if name[-1] in ['&', 'and', 'of']:
            name = name[:-1]
    return ' '.join(name)
