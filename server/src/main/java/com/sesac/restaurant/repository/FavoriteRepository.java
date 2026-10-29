package com.sesac.restaurant.repository;

import com.sesac.restaurant.domain.Favorite;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;

public interface FavoriteRepository extends JpaRepository<Favorite, Favorite.FavoriteId> {

    List<Favorite> findByIdUserIdOrderByCreatedAtDesc(Long userId);

    /** 즐겨찾기 목록 + 식당 요약. restaurants 는 읽기만 하므로 엔티티 대신 조인한 값을 바로 받는다. */
    @Query(value = """
            SELECT r.restaurant_id                    AS "restaurantId",
                   COALESCE(r.name_ko, r.name_official) AS "name",
                   r.country_code                     AS "countryCode",
                   r.region_1                         AS "region1",
                   f.created_at                       AS "favoritedAt"
              FROM app.favorites f
              JOIN public.restaurants r ON r.restaurant_id = f.restaurant_id
             WHERE f.user_id = :userId
             ORDER BY f.created_at DESC, r.restaurant_id
            """, nativeQuery = true)
    List<FavoriteView> findViewsByUserId(@Param("userId") Long userId);

    @Query(value = "SELECT EXISTS (SELECT 1 FROM public.restaurants WHERE restaurant_id = :restaurantId)",
            nativeQuery = true)
    boolean restaurantExists(@Param("restaurantId") Long restaurantId);

    /** 이미 있으면 아무것도 하지 않는다(같은 요청을 여러 번, 동시에 보내도 안전). */
    @Modifying
    @Query(value = """
            INSERT INTO app.favorites (user_id, restaurant_id) VALUES (:userId, :restaurantId)
            ON CONFLICT (user_id, restaurant_id) DO NOTHING
            """, nativeQuery = true)
    int insertIfAbsent(@Param("userId") Long userId, @Param("restaurantId") Long restaurantId);

    @Modifying
    @Query("DELETE FROM Favorite f WHERE f.id.userId = :userId AND f.id.restaurantId = :restaurantId")
    int deleteByUserAndRestaurant(@Param("userId") Long userId, @Param("restaurantId") Long restaurantId);

    interface FavoriteView {
        Long getRestaurantId();

        String getName();

        String getCountryCode();

        String getRegion1();

        Instant getFavoritedAt();
    }
}
