# Schedule each unscheduled team once per week
while unscheduled_teams:
    for date, slot, field in field_availability[week_start:week_start + 7]:
        slot_key = (date, slot, field)  # Unique identifier for date, time, and field

        # Skip this slot if it’s already used
        if slot_key in used_slots:
            continue

        # Schedule each unscheduled team once per week
        for i, (home, away) in enumerate(matchups[div]):
            if home in unscheduled_teams or away in unscheduled_teams:
                day_of_week = date.strftime('%a')
                
                # Check conditions for scheduling
                if (day_of_week in team_availability.get(home, set()) and
                    day_of_week in team_availability.get(away, set()) and
                    home not in used_slots and
                    away not in used_slots and
                    not has_double_header_this_week(game_counts, home, date) and
                    not has_double_header_this_week(game_counts, away, date)):

                    # Schedule game and update counts
                    schedule.append((date, slot, home, away, field))
                    game_counts[home].append(date)
                    game_counts[away].append(date)
                    used_slots.add(slot_key)  # Mark slot as used
                    matchups[div].pop(i)  # Remove scheduled matchup
                    print(f" - Scheduled: {home} vs {away} on {date.strftime('%Y-%m-%d')} at {slot} ({field})")

                    # Remove both teams from unscheduled list for this week
                    if home in unscheduled_teams:
                        unscheduled_teams.remove(home)
                    if away in unscheduled_teams:
                        unscheduled_teams.remove(away)
                    break  # Exit after scheduling one game in the slot
                else:
                    print(f" - Skipping: {home} vs {away} due to unavailability, double-header limit, or already scheduled today")

            # Exit if all teams are scheduled for the week
            if not unscheduled_teams:
                break
