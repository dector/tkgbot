from collections import defaultdict

import datetime
import requests

import secret
import forum
from models import Subscription, Node, NodeType
from database import db_session


class NewCommentsMessageBuilder:
    def __init__(self, maxsize):
        self._maxsize = maxsize
        self._reset()

    def _reset(self):
        self._list = []
        self._size = 0
        self._has_topics = False
        self._current_topic_has_comments = False
        self._current_topic = None
        self._current_header = None

    def _construct_topic_header(self, topic):
        l = []
        l.extend(['[', topic['name'], '](', forum.ROOT_LINK, topic['link'], ')'])
        if topic['section_name']:
            l.extend((' - ', '[',
                      topic['section_name'], '](', forum.ROOT_LINK,  topic['section_link'], ')\n'))
        return ''.join(l)

    def _construct_comment(self, comment):
        l = []
        l.extend([
            '*',
            comment['user_name'], ' wrote ', comment['date'].strftime('%d.%m.%y %H:%M'),
            '*',
            ' [link](', forum.ROOT_LINK, comment['link'], ') ',
            '[reply](', forum.ROOT_LINK, comment['reply_link'], ') ',
            '\n',
            comment['body'], '\n',
        ])
        return ''.join(l)

    def _append(self, msg_part):
        self._list.append(msg_part)
        self._size += len(msg_part)

    def get_message(self):
        if self._size:
            return ''.join(self._list)
        return None

    def add_comment(self, topic, comment):
        if self._current_topic is not topic or not self._current_header:
            self._current_header = self._construct_topic_header(topic)
            self._current_topic = topic
            self._current_topic_has_comments = False

        header_str = self._current_header if not self._current_topic_has_comments else ''
        blanks_before_header = '\n\n' if header_str and self._has_topics else ''
        blanks_before_comment = '\n' if self._current_topic_has_comments else ''
        comment_str = self._construct_comment(comment)

        msg_part = ''.join([blanks_before_header, header_str, blanks_before_comment, comment_str])

        if self._size+len(msg_part)<self._maxsize:
            self._append(msg_part)
            self._current_topic_has_comments = True
            self._has_topics = True
            return None
        else:
            if len(msg_part)>=self._maxsize:
                msg_part = msg_part[:self._maxsize-3]+'...'
            msg = self.get_message()
            self._list = []
            self._size = 0
            self._append(msg_part)
            self._current_topic_has_comments = True
            return msg


def send_message(chat_id, text):
    payload = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'Markdown',
            'disable_web_page_preview': True,
            }
    r = requests.post('https://api.telegram.org/bot%s/sendMessage' % secret.token, params=payload)


def send_message_new_comments(comment_updates):
    for user, updates in comment_updates.items():
        builder = NewCommentsMessageBuilder(maxsize=4000)
        for topic, comments in updates:
            for comment in comments:
                msg = builder.add_comment(topic, comment)
                if msg:
                    send_message(user, msg)
        msg = builder.get_message()
        if msg:
            send_message(user, msg)


def send_message_new_topics(topic_updates):
    for user, updates in topic_updates.items():
        msg_list = ['New topics:\n']
        for topic in updates:
            msg_list.extend(['[', topic['name'], '](', forum.ROOT_LINK, topic['link'], ')'])
            if topic['section_name']:
                msg_list.extend([' - [', topic['section_name'], '](', forum.ROOT_LINK, topic['section_link'], ')'])
            msg_list.append('\n')
        msg = ''.join(msg_list)
        send_message(user, msg)


def run():
    import pickle
    updates_comments_new = defaultdict(list)
    updates_topics_new = defaultdict(list)
    updated_topics = forum.get_updated_topics()
    for topic in updated_topics:
        # Find or create node for this topic
        node = Node.query.filter_by(id=topic['node_id']).first()
        if not node:
            node = Node(id=topic['node_id'], name=topic['name'])
            db_session.add(node)
        node.name = topic['name']
        #find parent of this node
        parent_node = node.parent
        if not parent_node:
            if topic['section_node_id']:
                parent_node = Node.query.filter_by(id=topic['section_node_id']).first()
                if not parent_node:
                    parent_node = Node(id=topic['section_node_id'],
                                       parent_id=NodeType.TOPIC.value,
                                       name=topic['section_name'])
                parent_node.name = topic['section_name']
            else:
                if topic['type'] in [NodeType.EVENT, NodeType.NEWS, NodeType.MATERIAL]:
                    parent_node = Node.query.filter_by(id=topic['type'].value).one()
                else:
                    raise Exception('Cannot find parent for node')
            node.parent = parent_node
        last_checked = node.last_checked
        node.last_checked = datetime.datetime.now()
        db_session.commit()

        current_node = node
        excepted = set()
        subscribed = {}
        while current_node:
            subscriptions = current_node.subscriptions
            for s in subscriptions:
                if s.exception:
                    excepted.add(s.chat_id)
                else:
                    if s.chat_id not in excepted and s.chat_id not in subscribed:
                        subscribed[s.chat_id] = s
            current_node = current_node.parent
        print(subscribed)

        for chat_id, subscription in subscribed.items():
            if topic['status']=='new' and not last_checked:
                updates_topics_new[chat_id].append(topic)
            if topic['new_comments_link'] and not subscription.no_comments:
                    comments = forum.get_new_comments_in_topic(topic['new_comments_link'])
                    if subscription.no_replies:
                        sub_comments = [comment for comment in comments if not comment['is_reply']]
                    else:
                        sub_comments = comments
                    if sub_comments:
                        updates_comments_new[chat_id].append((topic, sub_comments))

    send_message_new_topics(updates_topics_new)
    send_message_new_comments(updates_comments_new)


if __name__=='__main__':
    run()
