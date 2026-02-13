class Node:
    def __init__(self, word=None):
        self.children = {}
        self.word = word


class Trie:
    def __init__(self, words=None):
        self.root = Node()
        if words:
            for word in words:
                self.insert(word)

    def insert(self, word):
        node = self.root
        for char in word:
            if char not in node.children:
                node.children[char] = Node()
            node = node.children[char]
        node.word = word

    def dfs(self, node, token, results):
        if node.word:
            results.append([node.word, len(token) / len(node.word)])
        for _, child in node.children.items():
            self.dfs(child, token, results)

    def prefix_search(self, prefix):
        node = self.root
        for char in prefix:
            if char not in node.children:
                return []
            node = node.children[char]
        results = []
        self.dfs(node, prefix, results)
        return results


if __name__ == '__main__':
    t = Trie(['fowl'])
    t.insert('foo')
    t.insert('fooooot')
    t.insert('bar')
    t.insert('f')
    results = t.prefix_search('fo')
    rd = [
        {'symbol': symbol, 'score': score}
        for [symbol, score] in results
    ]
    print(rd)
