package com.sesac.restaurant.conversation;

import com.sesac.restaurant.common.ApiException;
import com.sesac.restaurant.domain.Conversation;
import com.sesac.restaurant.domain.Message;
import com.sesac.restaurant.repository.ConversationRepository;
import com.sesac.restaurant.repository.MessageRepository;
import com.sesac.restaurant.repository.UserRepository;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import tools.jackson.databind.json.JsonMapper;

import java.util.List;
import java.util.UUID;

@Service
public class ConversationService {

    /** 첫 질문으로 자동 제목을 만들 때의 최대 글자 수. */
    private static final int AUTO_TITLE_LENGTH = 30;

    private final ConversationRepository conversations;
    private final MessageRepository messages;
    private final UserRepository users;
    private final JsonMapper mapper;

    public ConversationService(ConversationRepository conversations, MessageRepository messages, UserRepository users,
                               JsonMapper mapper) {
        this.conversations = conversations;
        this.messages = messages;
        this.users = users;
        this.mapper = mapper;
    }

    @Transactional
    public ConversationResponse create(Long userId, UUID id, String title) {
        if (!users.existsById(userId)) {
            throw new ApiException(HttpStatus.UNAUTHORIZED, "USER_NOT_FOUND", "존재하지 않는 사용자입니다");
        }
        String finalTitle = (title == null || title.isBlank()) ? Conversation.DEFAULT_TITLE : title.trim();
        try {
            Conversation saved = conversations.saveAndFlush(new Conversation(
                    id != null ? id : UUID.randomUUID(), users.getReferenceById(userId), finalTitle));
            return ConversationResponse.from(saved);
        } catch (DataIntegrityViolationException e) {
            // 같은 id 가 이미 있다(주인이 누구든 같은 응답, 기존 대화는 건드리지 않는다)
            throw new ApiException(HttpStatus.CONFLICT, "DUPLICATE_CONVERSATION", "이미 사용 중인 대화 ID입니다");
        }
    }

    /** 이미 있는 id 가 남의 대화이면 409. 없거나 내 대화이면 통과한다(없으면 첫 질문 때 만든다). */
    @Transactional(readOnly = true)
    public void assertUsable(Long userId, UUID id) {
        if (conversations.findByIdAndUserId(id, userId).isEmpty() && conversations.existsById(id)) {
            throw new ApiException(HttpStatus.CONFLICT, "DUPLICATE_CONVERSATION", "이미 사용 중인 대화 ID입니다");
        }
    }

    /** 질문 한 턴의 시작: 대화가 없으면 만들고 사용자 메시지를 저장한다(한 트랜잭션). */
    @Transactional
    public void startTurn(Long userId, UUID id, String content) {
        if (conversations.findByIdAndUserId(id, userId).isEmpty()) {
            create(userId, id, null);
        }
        append(userId, id, Message.Role.user, content, null);
    }

    @Transactional(readOnly = true)
    public List<ConversationResponse> list(Long userId) {
        return conversations.findByUserIdOrderByUpdatedAtDesc(userId).stream().map(ConversationResponse::from).toList();
    }

    @Transactional
    public ConversationResponse rename(Long userId, UUID id, String title) {
        Conversation conversation = owned(userId, id);
        conversation.rename(title.trim());
        return ConversationResponse.from(conversation);
    }

    @Transactional
    public void delete(Long userId, UUID id) {
        if (conversations.deleteByIdAndUserId(id, userId) == 0) {
            throw notFound();
        }
    }

    @Transactional(readOnly = true)
    public List<MessageResponse> messages(Long userId, UUID id) {
        owned(userId, id);
        return messages.findByConversationIdOrderByCreatedAtAscIdAsc(id).stream()
                .map(m -> MessageResponse.from(m, mapper)).toList();
    }

    /**
     * 메시지를 저장한다. 챗봇 응답을 중계하는 서버 코드가 부르는 용도라 HTTP 로는 열어 두지 않았다
     * (클라이언트가 assistant 메시지를 직접 써 넣지 못하게). 대화의 첫 사용자 메시지로 제목을 자동으로 정한다.
     */
    @Transactional
    public MessageResponse append(Long userId, UUID id, Message.Role role, String content, List<Long> restaurantIds) {
        return append(userId, id, role, content, restaurantIds, null);
    }

    /** restaurantsJson 은 그 답변의 restaurants 이벤트 원문이다(카드·지도 복원용). */
    @Transactional
    public MessageResponse append(Long userId, UUID id, Message.Role role, String content, List<Long> restaurantIds,
                                  String restaurantsJson) {
        Conversation conversation = owned(userId, id);
        Long[] ids = (restaurantIds == null || restaurantIds.isEmpty()) ? null : restaurantIds.toArray(Long[]::new);
        Message saved = messages.saveAndFlush(new Message(conversation, role, content, ids, restaurantsJson));
        if (role == Message.Role.user && Conversation.DEFAULT_TITLE.equals(conversation.getTitle())) {
            conversation.rename(autoTitle(content));
        }
        conversation.touch();
        return MessageResponse.from(saved, mapper);
    }

    private Conversation owned(Long userId, UUID id) {
        return conversations.findByIdAndUserId(id, userId).orElseThrow(ConversationService::notFound);
    }

    /** 남의 대화와 없는 대화를 구분하지 않는다. */
    private static ApiException notFound() {
        return new ApiException(HttpStatus.NOT_FOUND, "CONVERSATION_NOT_FOUND", "대화를 찾을 수 없습니다");
    }

    private static String autoTitle(String content) {
        String oneLine = content.strip().replaceAll("\\s+", " ");
        return oneLine.length() <= AUTO_TITLE_LENGTH ? oneLine : oneLine.substring(0, AUTO_TITLE_LENGTH) + "…";
    }
}
