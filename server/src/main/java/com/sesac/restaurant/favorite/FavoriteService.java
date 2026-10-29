package com.sesac.restaurant.favorite;

import com.sesac.restaurant.common.ApiException;
import com.sesac.restaurant.repository.FavoriteRepository;
import com.sesac.restaurant.repository.UserRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
public class FavoriteService {

    private final FavoriteRepository favorites;
    private final UserRepository users;

    public FavoriteService(FavoriteRepository favorites, UserRepository users) {
        this.favorites = favorites;
        this.users = users;
    }

    @Transactional(readOnly = true)
    public List<FavoriteResponse> list(Long userId) {
        return favorites.findViewsByUserId(userId).stream().map(FavoriteResponse::from).toList();
    }

    /** 이미 즐겨찾기한 식당이면 그대로 둔다(멱등). */
    @Transactional
    public void add(Long userId, Long restaurantId) {
        if (!users.existsById(userId)) {
            throw new ApiException(HttpStatus.UNAUTHORIZED, "USER_NOT_FOUND", "존재하지 않는 사용자입니다");
        }
        if (!favorites.restaurantExists(restaurantId)) {
            throw new ApiException(HttpStatus.NOT_FOUND, "RESTAURANT_NOT_FOUND", "존재하지 않는 식당입니다");
        }
        favorites.insertIfAbsent(userId, restaurantId);
    }

    /** 즐겨찾기에 없는 식당이어도 성공으로 본다(멱등). */
    @Transactional
    public void remove(Long userId, Long restaurantId) {
        favorites.deleteByUserAndRestaurant(userId, restaurantId);
    }
}
