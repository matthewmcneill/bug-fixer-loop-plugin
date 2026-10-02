import re

def resolve_file(filepath, merge_func):
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Simple regex to find conflict blocks
    pattern = re.compile(r'<<<<<<< HEAD\n(.*?)\n=======\n(.*?)\n>>>>>>> origin/main\n', re.DOTALL)
    
    def repl(match):
        head = match.group(1)
        main = match.group(2)
        return merge_func(filepath, head, main)
        
    new_content = pattern.sub(repl, content)
    
    with open(filepath, 'w') as f:
        f.write(new_content)

def merger(filepath, head, main):
    if filepath == 'docs/label-taxonomy.md':
        if 'bug-fixer:system:pr' in head:
            # take main which changed Engineer to System
            return main + '\n'
    elif filepath == 'skills/bug-fixer-engineering/EVAL.md':
        # HEAD has the added bug-fixer:architect:awaiting-review
        return head + '\n'
    elif filepath == 'skills/bug-fixer-engineering/SKILL.md':
        # HEAD has the added bug-fixer:architect:awaiting-review
        return head + '\n'
    return head + '\n'

resolve_file('docs/label-taxonomy.md', merger)
resolve_file('skills/bug-fixer-engineering/EVAL.md', merger)
resolve_file('skills/bug-fixer-engineering/SKILL.md', merger)
