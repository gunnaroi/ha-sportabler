"""Read-only GraphQL operations observed in the Abler web client."""

QUERY_NEWS_FEED = """
query myNewsFeed($first: Int, $after: String) {
  myNewsFeed(first: $first, after: $after) {
    pageInfo {
      ...PageInfoFragment
      __typename
    }
    edges {
      node {
        ...RootPostFragment
        __typename
      }
      __typename
    }
    __typename
  }
}

fragment PageInfoFragment on PageInfoGraphql {
  startCursor
  endCursor
  hasNextPage
  hasPreviousPage
  __typename
}

fragment RootPostFragment on MessageChannelRootPost {
  ...PostBaseFragment
  commentsAllowed
  audienceScopes
  attachments {
    ...AttachmentFragment
    __typename
  }
  mediaAttachments {
    ...AttachmentFragment
    __typename
  }
  flags {
    ...PostFlags
    __typename
  }
  relationships {
    ...PostRelationshipProfile
    __typename
  }
  __typename
}

fragment PostBaseFragment on MessageChannelPost {
  id
  authorProfile {
    ...PostAuthorProfileFragment
    __typename
  }
  authorRole
  audienceProfile {
    ...PostAudienceProfileFragment
    __typename
  }
  counters {
    ...PostCounter
    __typename
  }
  myReactions
  body
  createdAt
  channelType
  channels
  __typename
}

fragment PostAuthorProfileFragment on PostAuthorProfile {
  id
  postAs
  name
  picture
  __typename
}

fragment PostAudienceProfileFragment on PostAudienceProfile {
  id
  audienceType
  name
  picture
  __typename
}

fragment PostCounter on MessageChannelPostCounter {
  type
  count
  __typename
}

fragment AttachmentFragment on AttachmentGraphql {
  link
  relativeLink
  path
  filename
  filesize
  mimetype
  __typename
}

fragment PostFlags on PostFlags {
  canEdit
  canDelete
  isPinned
  __typename
}

fragment PostRelationshipProfile on PostRelationshipProfile {
  id
  name
  picture
  __typename
}
"""

QUERY_POST_COMMENTS = """
query getPostComments($postId: Int!, $first: Int, $after: String) {
  getPostComments(rootPostId: $postId, first: $first, after: $after) {
    pageInfo {
      startCursor
      endCursor
      hasNextPage
      hasPreviousPage
      __typename
    }
    edges {
      node {
        ...CommentFragment
        __typename
      }
      __typename
    }
    __typename
  }
}

fragment CommentFragment on MessageChannelNestedPost {
  id
  authorProfile {
    ...PostAuthorProfileFragment
    __typename
  }
  authorRole
  body
  counters {
    ...PostCounter
    __typename
  }
  myReactions
  createdAt
  flags {
    ...PostNestedFlags
    __typename
  }
  __typename
}

fragment PostAuthorProfileFragment on PostAuthorProfile {
  id
  postAs
  name
  picture
  __typename
}

fragment PostCounter on MessageChannelPostCounter {
  type
  count
  __typename
}

fragment PostNestedFlags on PostNestedFlags {
  canEdit
  canDelete
  __typename
}
"""

QUERY_CONVERSATION_MESSAGES = """
query conversationMessages($pagination: PaginationType!, $conversationIds: [ID!]) {
  conversationMessages(pagination: $pagination, conversationIds: $conversationIds) {
    edges {
      node {
        id
        creator { id displayName initials picture ablerUser __typename }
        messageBody
        attachments { id path fileName description contentType status __typename }
        createdAt
        recipient { id isRead __typename }
        __typename
      }
      __typename
    }
    pageInfo { hasNextPage hasPreviousPage startCursor endCursor __typename }
    __typename
  }
}
"""

QUERY_CONVERSATIONS = """
query message($id: String, $first: Int, $cursor: String) {
  message(id: $id, after: $cursor, first: $first) {
    edges {
      node {
        id
        name
        user1 { id displayName }
        user2 { id displayName }
        membersCount
        conversationType
        messageGroup { id name }
        unreadCount
      }
    }
    pageInfo { hasNextPage hasPreviousPage startCursor endCursor }
  }
}
"""
