def schedule_doubleheaders_preemptively(unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
                                        team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents,
                                        used_slots, schedule=None):
    if schedule is None:
        schedule = []
        
    # Process each available date.
    for d in sorted(timeslots_by_date.keys()):
        day_of_week = d.strftime('%a')
        week_num = d.isocalendar()[1]
        slots = timeslots_by_date[d]
        if not slots:
            continue

        # Process each team that still needs a doubleheader.
        for team in list(team_stats.keys()):
            if doubleheader_count[team] >= MIN_DOUBLE_HEADERS:
                continue
            if day_of_week not in team_availability.get(team, set()):
                continue
            if d in team_blackouts.get(team, set()):
                continue

            games_today = team_game_days[team].get(d, 0)
            # Case 1: No game scheduled yet – try to schedule a doubleheader (2 games) on an empty day.
            if games_today == 0:
                if len(slots) < 2:
                    continue
                for i in range(len(slots) - 1):
                    slot1 = slots[i]
                    slot2 = slots[i+1]
                    free_fields_slot1 = [entry for entry in field_availability 
                                           if entry[0].date() == d and entry[1] == slot1 and ((entry[0], slot1, entry[2]) not in used_slots)]
                    free_fields_slot2 = [entry for entry in field_availability 
                                           if entry[0].date() == d and entry[1] == slot2 and ((entry[0], slot2, entry[2]) not in used_slots)]
                    if not free_fields_slot1 or not free_fields_slot2:
                        continue

                    # Find two distinct matchups from unscheduled that involve the team.
                    candidate_matchups = [m for m in unscheduled if team in m]
                    if len(candidate_matchups) < 2:
                        continue

                    found = False
                    for combo in itertools.combinations(candidate_matchups, 2):
                        m1, m2 = combo
                        opp1 = m1[0] if m1[1] == team else m1[1]
                        opp2 = m2[0] if m2[1] == team else m2[1]
                        if opp1 == opp2:
                            continue
                        # Check that each opponent is available and not blacked out.
                        if day_of_week not in team_availability.get(opp1, set()) or d in team_blackouts.get(opp1, set()):
                            continue
                        if day_of_week not in team_availability.get(opp2, set()) or d in team_blackouts.get(opp2, set()):
                            continue
                        # Ensure opponents are not already scheduled on d.
                        if team_game_days[opp1].get(d, 0) != 0 or team_game_days[opp2].get(d, 0) != 0:
                            continue
                        # Check weekly limits: team gets +2, opponents get +1 each.
                        if team_stats[team]['weekly_games'][week_num] + 2 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp1]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp2]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue

                        # Determine home/away for each game.
                        home1, away1 = decide_home_away(team, opp1, team_stats)
                        home2, away2 = decide_home_away(team, opp2, team_stats)
                        entry1 = free_fields_slot1[0]
                        entry2 = free_fields_slot2[0]
                        date1, slot1_str, field1 = entry1
                        date2, slot2_str, field2 = entry2

                        # Schedule the two games.
                        unscheduled.remove(m1)
                        unscheduled.remove(m2)
                        team_stats[home1]['home_games'] += 1
                        team_stats[away1]['away_games'] += 1
                        team_stats[home2]['home_games'] += 1
                        team_stats[away2]['away_games'] += 1

                        game1 = (date1, slot1_str, field1, home1, home1[0], away1, away1[0])
                        game2 = (date2, slot2_str, field2, home2, home2[0], away2, away2[0])
                        schedule.append(game1)
                        schedule.append(game2)

                        # Update stats for team and opponents.
                        for t in [team, opp1]:
                            team_stats[t]['total_games'] += 1
                            team_stats[t]['weekly_games'][week_num] += 1
                            team_game_days[t][d] += 1
                            team_game_slots[t][d].append(slot1_str)
                        for t in [team, opp2]:
                            team_stats[t]['total_games'] += 1
                            team_stats[t]['weekly_games'][week_num] += 1
                            team_game_days[t][d] += 1
                            team_game_slots[t][d].append(slot2_str)
                        doubleheader_count[team] += 1
                        team_doubleheader_opponents[team][d].update([opp1, opp2])
                        used_slots[(date1, slot1_str, field1)] = True
                        used_slots[(date2, slot2_str, field2)] = True
                        found = True
                        break
                    if found:
                        break

            # Case 2: Already one game scheduled today – try to add a game to form a doubleheader.
            elif games_today == 1:
                # Find the already scheduled game for the team on day d.
                current_slot = team_game_slots[team][d][0]
                try:
                    idx = slots.index(current_slot)
                except ValueError:
                    continue
                if idx + 1 >= len(slots):
                    continue
                next_slot = slots[idx + 1]
                free_fields_next = [entry for entry in field_availability 
                                     if entry[0].date() == d and entry[1] == next_slot and ((entry[0], next_slot, entry[2]) not in used_slots)]
                if not free_fields_next:
                    continue

                # Identify the already scheduled opponent.
                already_opponent = None
                for game in schedule:
                    # game tuple: (date, slot, field, home, home_div, away, away_div)
                    if game[0].date() == d and (game[3] == team or game[5] == team):
                        already_opponent = game[5] if game[3] == team else game[3]
                        break
                if already_opponent is None:
                    continue

                candidate_matchups = [m for m in unscheduled if team in m]
                if not candidate_matchups:
                    continue

                found = False
                for m in candidate_matchups:
                    opp = m[0] if m[1] == team else m[1]
                    if opp == already_opponent:
                        continue
                    if day_of_week not in team_availability.get(opp, set()) or d in team_blackouts.get(opp, set()):
                        continue
                    if team_game_days[opp].get(d, 0) != 0:
                        continue
                    if team_stats[team]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                        continue
                    if team_stats[opp]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                        continue

                    home, away = decide_home_away(team, opp, team_stats)
                    entry = free_fields_next[0]
                    date_entry, slot_str, field = entry
                    unscheduled.remove(m)
                    team_stats[home]['home_games'] += 1
                    team_stats[away]['away_games'] += 1
                    game = (date_entry, slot_str, field, home, home[0], away, away[0])
                    schedule.append(game)
                    for t in [team, opp]:
                        team_stats[t]['total_games'] += 1
                        team_stats[t]['weekly_games'][week_num] += 1
                        team_game_days[t][d] += 1
                        team_game_slots[t][d].append(slot_str)
                    doubleheader_count[team] += 1
                    team_doubleheader_opponents[team][d].add(opp)
                    used_slots[(date_entry, slot_str, field)] = True
                    found = True
                    break
                if found:
                    continue

    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, unscheduled
