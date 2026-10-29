package com.sesac.restaurant.favorite;

import com.sesac.restaurant.repository.FavoriteRepository.FavoriteView;

import java.time.Instant;

public record FavoriteResponse(Long restaurantId, String name, String countryCode, String region1, Instant favoritedAt) {

    static FavoriteResponse from(FavoriteView v) {
        return new FavoriteResponse(v.getRestaurantId(), v.getName(), v.getCountryCode(), v.getRegion1(), v.getFavoritedAt());
    }
}
